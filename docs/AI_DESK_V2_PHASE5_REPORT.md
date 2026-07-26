# AI Desk V2 — Phase 5 Report

Branch: `feature/owner-authorized-handoff-v2` (based on `origin/MVP`). This
phase had two mandatory parts: **Part A**, a Phase 4 repair gate (must pass
before any Phase 5 work began), and **Part B**, release-candidate demo
polish. Nothing in this phase committed or pushed anything; no branch other
than the one supplied was touched; no Dashboard integration, real BLE,
radar serial access, OCR, notifications, background screen monitoring, or
new external API was added.

Baseline reproduced at the start of this phase: `python -B -m unittest
discover -s tests -v` → **408 tests, 405 passed, 3 skipped, 0 failed** —
matched the expected baseline exactly.

---

## Part A — Phase 4 repair gate

All nine audited defects were fixed in code, with adversarial regression
tests for each. Checkpoint after Part A: **452 tests, 449 passed, 3
skipped, 0 failed** (444 new/changed tests across this phase's work; the
final count after Part B is 457 — see "Complete test results" below).

### 1. PACKAGE_INSTALL YES bound to exactly what the owner saw

**Defect:** `PermissionGate.grant_from_answer` accepted caller-supplied
`package_specs`/`argv`/`duplicate_path` without checking them against the
pending `PermissionQuestion` — a caller could, in principle, grant a permit
for a different command than the one actually displayed.

**Fix** (`execution/permission.py`, `domain/question.py`):
- `PermissionQuestion` gained a required `duplicate_path` field (both
  `build_codex_data_question` and `build_package_install_question` now
  bind it).
- `grant_from_answer` now validates, BEFORE `compute_digest`/`submit_answer`
  are ever called: `duplicate_path` must equal `pending.duplicate_path`;
  for PACKAGE_INSTALL, `package_specs`/`argv` must exactly equal
  `pending.package_names`/`pending.proposed_argv`; for CODEX_DATA,
  `package_specs`/`argv` must both be empty. Any mismatch raises
  `PermissionContextMismatchError` — no transition is attempted, no permit
  is created, state remains `PERMISSION_PENDING`.
- `PermissionGate.consume` now requires `question_id` (previously
  optional).
- Regression test proves the exact adversarial scenario: a question
  displaying `safe==1`/`safe-command` cannot be exploited by calling
  `grant_from_answer` with `evil==9`/`evil-command` — rejected before any
  state change, and the evil command is never consumable afterward
  (`PackageInstallGrantBindingTest`).

### 2. Pre-write path-safety validation in the demo builder

**Defect:** `build_demo_orchestrator` called `session_root.mkdir(...)` and
constructed `OwnerHandoffStore(db_path)` (which itself creates the parent
directory) before validating those paths were safe relative to the source.

**Fix** (`workspace/path_policy.py`, `run_owner_handoff_demo.py`): new pure
`validate_demo_output_paths(source, allowed_boundary, session_root,
db_path)` — no filesystem mutation whatsoever — checked BEFORE any
`mkdir`/SQLite open. Rejects `session_root == source`, `session_root` under
`source`, `source` under `session_root`, and `db_path` under `source`.
`tests/test_demo_path_safety.py` proves each rejection leaves the source
tree byte-for-byte, tree-for-tree unchanged (no new file/directory
anywhere inside it).

### 3. Real cancellable execution session (replacing `ThreadPoolExecutor`)

**Defect:** the previous `run_current_step` used
`with concurrent.futures.ThreadPoolExecutor(...) as pool:` — the context
manager's exit (and `shutdown(wait=True)`) blocks until every submitted
task finishes, including one that never will; `future.cancel()` cannot
actually stop a running function; and `concurrent.futures.thread`
registers an interpreter-exit hook that joins its worker threads even
after `shutdown(wait=False)`, so a stuck task could hang process exit
itself.

**Fix** (`return_coordinator.py`): new `ExecutionSession` runs the step on
a plain `daemon=True` thread — `wait(timeout)` is always bounded and
returns even if the thread is still running; a daemon thread can never
block interpreter exit. `run_current_step` uses it directly (no
`ThreadPoolExecutor` anywhere). `CodexCLIExecutor.execute` additionally
accepts `CodingTaskRequest.stop_requested` (a live callable) and checks it
between nested Codex JSONL work items: once return is confirmed, it lets
any currently-open item finish, refuses to accept a new `item.id`, and
retains the live process handle (`terminate_current()`) so the
coordinator's `terminate_fn` can reach it for a SIGTERM →
(bounded wait) → SIGKILL escalation. `MultiStepFakeCodingExecutor` gives
the same cooperative-stop contract to a deterministic, non-subprocess fake.

Required timing test (`test_non_cooperative_step_bounded_by_grace_period_not_by_step_duration`):
return grace = 0.02s, a genuinely non-cooperative fake step that sleeps
0.25s and never checks any stop signal — the coordinator call returns/fails
within ~0.1s (not 0.25s), and no subsequent step starts; return is
triggered from a background timer *after* the step has actually started
running (not before `run_current_step` is called), exactly as required.

### 4. Fail closed on original-workspace verification failure

**Defect:** `ReturnCoordinator.finalize` always transitioned to
READY_FOR_REVIEW and always returned a `ResumeReport`, even when
`verify_original_unchanged` failed — the report's `status` field said
`"failed_verification"`, but the task state and the report's very
existence implied a clean, reviewable result.

**Fix** (`domain/resume.py`, `return_coordinator.py`,
`state_machine.py`): `ResumeReport.__post_init__` now rejects any
non-empty `original_files_modified` — a `ResumeReport` can only exist
after verification passed. A new `SafetyFailureRecord` (a different,
incompatible type) is returned instead when verification fails, and the
task transitions to FAILED (never READY_FOR_REVIEW). `RETURN_REQUESTED`
was added to `RUNTIME_BOUND_STATES` so FAILED is an allowed target from it
too. The pre-existing test that asserted READY_FOR_REVIEW with a
non-empty `original_files_modified` was rewritten
(`test_finalize_with_tampered_original_fails_closed_never_ready_for_review`)
to assert the corrected behavior.

### 5. Detect prohibited duplicate changes

**Defect:** `scan_duplicate_changes` only reported created/modified files
— a deleted or renamed-away copied file, a symlink introduced into the
duplicate, or a file<->directory type change were silently omitted (the
fixed `ResumeReport` shape has no field for any of them).

**Fix** (`workspace/duplicator.py`, `domain/manifest.py`,
`orchestrator.py`): `scan_duplicate_changes` now detects all of the above
plus a missing duplicate root, setting `duplicate_unsafe=True` with a
human-readable `duplicate_unsafe_reason`. `finalize_return` checks this
BEFORE calling `ReturnCoordinator.finalize` and, if set, transitions
straight to FAILED with a `SafetyFailureRecord` — never a normal report.
Regression test proves deleting `duplicate/main.py` is detected and can
never produce READY_FOR_REVIEW
(`test_deletion_of_duplicate_main_py_fails_closed_never_ready_for_review`).

### 6. Orchestrator terminal/error behavior

**Defects and fixes** (`orchestrator.py`, `run_owner_handoff_demo.py`):
- D and UNSUPPORTED now reliably reach CANCELED; the demo's `select`
  handler no longer calls `routed_skill()` after D (which would have
  raised `NoRoutedOptionError` and crashed the command), and now always
  calls `execute_authorized_task()` for UNSUPPORTED so the task doesn't
  get stuck in AUTHORIZED.
- New `acknowledge_terminal_task()` frees the orchestrator for a new
  `start_task()` once the current task is CANCELED/FAILED/RETURNED by any
  path — previously only the READY_FOR_REVIEW → RETURNED happy path
  (`deliver_control`) cleared task state, so a task that canceled or
  failed any other way left the orchestrator permanently "busy."
- `begin_coding_workspace_duplication`, `execute_coding_task`, and
  `_execute_research` all now transition to FAILED (via a shared
  `_fail_current_task` helper) on a duplication exception, a FAILED
  `ExecutionResult`, or a `StepDidNotFinishInGracePeriodError`.
- `run_owner_handoff_demo.py` now catches every exception (not only
  `DemoCommandError`) at the command-dispatch boundary and prints a
  single sanitized `error: Type: message` line, never a raw traceback;
  `run_commands` returns `False` if any command errored, and `main`'s
  scripted mode exits non-zero in that case; `status` is safe with no
  active task.
- Demo-level tests (not just direct orchestrator tests) added for D,
  UNSUPPORTED, out-of-order commands, a failing install, `acknowledge`,
  and a second task after acknowledgment (`DemoRobustnessTest`, 9 tests).

### 7. Restart and persistence

**Fix:** `RUNTIME_BOUND_STATES` now includes `RETURN_REQUESTED` (also
closes item 4's FAILED-from-RETURN_REQUESTED requirement), so restart
recovery fails it closed exactly like EXECUTING/WORKSPACE_DUPLICATING/
PERMISSION_PENDING. New `persistence.py` (`TaskArtifactStore`) writes the
duplication manifest, the routed selected task, the sanitized last
execution result, approved package installs, and the final report/failure
as sanitized (redacted, never a secret/token/raw environment value),
atomically-written (temp file + `os.replace`) JSON under
`<session_root>/<task_id>.artifacts/` — never inside the original source.
`finalize_return` persists the manifest/selected-task/execution-result
before attempting the READY_FOR_REVIEW/FAILED transition, and the final
report/failure immediately after building it. New
`OwnerHandoffOrchestrator.attach_to_task` + `load_persisted_final_report`
let a fresh process reload and display the exact report for a task that
reached READY_FOR_REVIEW in a prior run — proven with a same-process
simulated-restart test (a fresh orchestrator instance sharing only the
store/session_root). Nothing is auto-deleted.

### 8. Subprocess lifecycle and output bounds

**Defect:** `SubprocessCodexProcessRunner` merged stderr into stdout
(`stderr=subprocess.STDOUT`) despite the class docstring describing
separate handling, and used an unbounded `queue.Queue()`.

**Fix** (`execution/codex_cli.py`): stdout and stderr are now captured on
separate pipes and separate reader threads; stdout uses a bounded
`queue.Queue(maxsize=500)`; stderr accumulates into a bounded
(20,000-char), sanitized-on-read tail buffer and is never parsed as JSONL.
`terminate()` now escalates SIGTERM → (bounded 5s wait) → SIGKILL on
POSIX, with every wait bounded (never an indefinite second wait). A
runner-start exception (e.g. the binary vanishing between preflight and
launch) becomes a sanitized `ExecutionResult(FAILED)` rather than a raw
propagated exception; the CODEX_DATA permit — already consumed before the
launch attempt — stays consumed, so a retry requires a fresh YES
(`test_runner_start_failure_is_sanitized_and_permit_stays_consumed`). No
real Codex process is invoked by any automated test.

### 9. Remaining privacy leaks

**Defect:** `ResearchAgentExecutor.execute` interpolated raw exception
text and the A2A `executive_summary` field directly into
`ExecutionResult.summary` with no redaction.

**Fix** (`execution/research_executor.py`): both are now passed through
`redact_sensitive` before being placed in `summary`. Tests with a
token/API-key-like string in both the exception message and the executive
summary confirm the secret never appears in the result.

---

## Part B — Release-candidate demo polish

### 1. Visibly meaningful default fake coding demo

New `DemoCalculatorFixExecutor` (`run_owner_handoff_demo.py`, demo-only —
the general `FakeCodingExecutor` used across the test suite is
unchanged): fixes the exact seeded bug in
`demo_workspace/sample_project/calculator.py` (`return a - b` →
`return a + b`) inside the duplicate only, or creates one clearly-named
`AI_GENERATED_NOTE.txt` if that exact fixture/bug isn't present (never
guesses at an arbitrary edit). It requires and consumes the same
CODEX_DATA permit the real Codex path would, and checks
`request.stop_requested()` before its one step. This is now the default
for `--executor fake`. Verified: the duplicate is fixed, the source
fixture remains byte-for-byte unchanged (still containing the original
bug), and `ResumeReport.files_modified_in_duplicate` lists
`calculator.py`.

### 2. Automatic package-install tracking

`OwnerHandoffOrchestrator.install_package` now accumulates the exact
normalized package specs from every successful, permitted install into
per-task state (`_TaskState.packages_installed`); `finalize_return` reads
this directly rather than accepting a caller-supplied list. A
failed/denied/mismatched install never appears. The demo continues using
a fake installer; no real installation was performed.

### 3. Demonstrable, responsive Terminal flow

New `run` command starts the routed coding/research step on a background
`ExecutionSession` and returns to the prompt immediately; new `wait
[timeout_seconds]` blocks for the result. `status` and `return` remain
usable while a step is running (proven by a scripted sequence issuing
`status` between `run` and `wait`). `status` now reports task/state/
routed-skill/duplicate-path/return-requested/execution-status without
exposing secrets, and is safe to call with no active task. `--workspace`
remains mandatory in every mode.

### 4. Safe preflight-only mode

New `--preflight-only` flag and `run_preflight_only()` function: runs only
`codex --version` / `codex exec --help` and reports supported/missing
isolation flags, then exits. Structurally never constructs an
orchestrator, `OwnerHandoffStore`, or workspace duplicator — proven by a
test that patches `OwnerHandoffStore.__init__` to raise if ever called and
confirms `run_preflight_only` still completes normally. Automated tests
use only fake command runners.

### 5. Documentation corrections

Corrected in `README.md` and `application/owner_handoff/README.md`:
stale "Reserved; no coding executor exists yet" / "no workspace duplicator
exists yet" config-table notes; the return coordinator's description
(bounded grace via a real cancellable session, not a `ThreadPoolExecutor`);
verification-failure behavior (FAILED + `SafetyFailureRecord`, never
READY_FOR_REVIEW); exactly what the default fake demo now changes; the
real-vs-fake package-install distinction; return/cancellation semantics
and their honest limits; where task artifacts are now persisted; why
post-hoc command inspection is defense-in-depth, not prevention; and that
`--workspace` accepts any path-policy-valid directory, not only the
checked-in fixture. Every fixed/default parameter (idle threshold, leave
confirmation, question expiry, Codex runtime/step-cap, return grace, step
poll interval, post-terminate wait, stdout queue size/stderr tail buffer,
SIGTERM→SIGKILL wait, preflight timeout, package-install timeout, A2A
timeout) is now listed in the root README's "Hard-coded defaults" table.

### 6. Artifact hygiene

Verified (see "Final verification" below): no test leaves a new
`demo_workspace/sample_project/__pycache__`, SQLite database, session
directory, report, `.env`, screenshot, log, or temporary command file
inside the repository. Every test uses `tempfile.TemporaryDirectory()` or
an explicit out-of-repo path for `session_root`/`db_path`; `python -B` is
used throughout to disable bytecode generation for fixture subprocesses.

---

## Complete test results

```
python -B -m unittest discover -s tests -v
Ran 457 tests
OK (skipped=4)
```

- 453 passed, 0 failed, 4 skipped (1 pre-existing OpenAI integration test
  requiring `RUN_OPENAI_INTEGRATION=1`; 3 symlink-creation tests skipping
  on this Windows development environment, which does not permit
  unprivileged symlink creation — one more than Phase 4's baseline because
  this phase added one additional symlink-introduced-into-duplicate
  regression test that skips identically).
- New/changed test files this phase: `tests/test_demo_path_safety.py` (6
  tests), `tests/test_cancellable_execution.py` (7 tests),
  `tests/test_persistence.py` (8 tests), plus new tests added to
  `tests/test_permission_flow.py`, `tests/test_return_coordinator.py`,
  `tests/test_workspace_duplicator.py`, `tests/test_research_executor.py`,
  `tests/test_wearable_contract.py` (fixture fix only), and a large new
  set of demo-level tests in `tests/test_owner_handoff_e2e.py`
  (`DemoRobustnessTest`, `DemoPreflightOnlyTest`, `RestartPersistenceTest`).
- Confirmed stable across repeated runs of `tests/test_owner_handoff_e2e.py`
  (no flakiness from the background-thread `run`/`wait` demo commands).
- `git diff --check`: only a benign LF/CRLF warning on `.env.example`.
  `requirements.txt` diff: empty.

## State-transition changes

- `RUNTIME_BOUND_STATES` gained `RETURN_REQUESTED` — restart recovery and
  `ALLOWED_SOURCES[FAILED]` both changed accordingly (no other state-table
  edit was needed).
- New terminal path: any runtime-bound state → FAILED via
  `_fail_current_task`, on duplication failure, executor failure, or
  grace-period timeout (previously only reachable via restart recovery).

## Cancellation semantics (final)

One atomic step = one `CodingAgentExecutor`/`ResearchAgentExecutor.execute()`
call. A cooperative executor polls `stop_requested` between its own
internal steps and returns promptly; `ExecutionSession`'s bounded
`daemon=True`-thread wait is the backstop for one that doesn't (or can't).
`terminate_fn`, when supplied (real Codex only), performs a real
SIGTERM→SIGKILL escalation. An uncancelable step (e.g. real A2A) is
reported as "did not finish in time," never falsely "terminated."

## Persistence format

JSON, sanitized (`redact_sensitive` applied recursively to every string),
written atomically under `<session_root>/<task_id>.artifacts/`:
`manifest.json`, `selected_task.json`, `execution_result_*.json`,
`packages_installed.json`, `final_report.json`.

## Permission binding (final)

A CODEX_DATA/PACKAGE_INSTALL permit is single-use, bound to the exact
task/question/session/duplicate-path/live-digest/(package specs+argv for
PACKAGE_INSTALL) it was granted for, and — new this phase — validated
against the pending question's own `duplicate_path`/`package_names`/
`proposed_argv` at grant time, before any state transition, so a YES can
never be stretched to cover anything the owner didn't actually see.

## Deterministic demo transcript (fake mode)

```
> start demo-task
started task 'demo-task' (state=OBSERVING)
> away
owner away confirmed (state=OWNER_LEFT_CONFIRMED)
> ask
[... full question rendered ...]
> select A
answer accepted=True state=AUTHORIZED
routed skill: coding
duplicate created at: <session_root>/<session-id>
[... CODEX_DATA permission prompt rendered ...]
> grant
answer accepted=True state=EXECUTING
CODEX_DATA granted; state=EXECUTING -- use 'run' to start coding
> run
execution started in the background -- 'status'/'return' remain available; use 'wait' to block until it finishes
> status
task=demo-task state=EXECUTING skill=coding duplicate=<...> return_requested=False execution=running in background
> wait
execution result: completed -- fixed calculator.py: add(a, b) now returns a + b
> finalize Fixed the calculator add bug
{
  "selected_task": "Fixed the calculator add bug",
  "work_completed": [],
  "files_created": [],
  "files_modified_in_duplicate": ["calculator.py"],
  "original_files_modified": [],
  "packages_installed": [],
  "status": "ready_for_review"
}
> deliver
control returned to owner
```

## Files changed (Phase 5)

**New:** `application/owner_handoff/persistence.py`,
`tests/test_demo_path_safety.py`, `tests/test_cancellable_execution.py`,
`tests/test_persistence.py`, `docs/AI_DESK_V2_PHASE5_REPORT.md` (this
file).

**Modified:** `application/owner_handoff/execution/permission.py`,
`application/owner_handoff/domain/question.py`,
`application/owner_handoff/workspace/path_policy.py`,
`application/owner_handoff/return_coordinator.py`,
`application/owner_handoff/domain/resume.py`,
`application/owner_handoff/domain/manifest.py`,
`application/owner_handoff/workspace/duplicator.py`,
`application/owner_handoff/state_machine.py`,
`application/owner_handoff/orchestrator.py`,
`application/owner_handoff/execution/codex_cli.py`,
`application/owner_handoff/execution/coding_executor.py`,
`application/owner_handoff/execution/research_executor.py`,
`run_owner_handoff_demo.py`, `README.md`,
`application/owner_handoff/README.md`, plus corresponding updates to
`tests/test_permission_flow.py`, `tests/test_codex_executor.py`,
`tests/test_return_coordinator.py`, `tests/test_workspace_duplicator.py`,
`tests/test_wearable_contract.py`, `tests/test_research_executor.py`,
`tests/test_owner_handoff_e2e.py`.

`.env.example` and `requirements.txt` were **not** changed — no new
genuinely-configurable value was introduced this phase either (every new
timing/bound value is a documented fixed constant, consistent with how
Phase 3/4's equivalents were handled).

## Remaining honest limitations

- No macOS hardware verification and no real-Codex-CLI verification have
  been performed at any point in this project.
- Return/cancellation for a genuinely uncancelable remote step (real A2A)
  can only be bounded, never guaranteed-terminated; this is reported
  honestly, not hidden.
- Restart/persistence reload is verified only via same-process simulated
  restarts (a fresh orchestrator instance sharing the same store/session
  root), not a real separate-process restart drill.
- Post-hoc Codex JSONL inspection (including the prohibited-command
  check) remains defense-in-depth, not prevention, by construction.
- The demo's `run`/`wait` background execution is intentionally simple
  (one `ExecutionSession` per step); it does not implement a general job
  queue or support running two tasks' steps concurrently (the
  single-active-task rule already prevents that from being needed).

## Exact commands for a later, separately approved macOS real-Codex rehearsal

Not run in this session. For a future, explicitly authorized rehearsal:

```bash
codex --version
codex exec --help

python3 run_owner_handoff_demo.py \
  --workspace demo_workspace/sample_project \
  --executor codex \
  --preflight-only

python3 run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace/sample_project \
  --executor codex
```

Followed by manual, human review of the resulting duplicate/diff before
trusting or applying anything from it — never automatic.

---

Stopping here for human review, per the instructions. No commit or push
was performed at any point in this phase.
