# AI Desk V2 — Phase 3 Report

Scope: (A) a repair gate fixing seven independently verified Phase 2
defects, gated behind a full green test run before any Phase 3 code was
written; then (B) conservative skill routing, physical workspace
duplication with path-safety policy, the CODEX_DATA/PACKAGE_INSTALL
permission flows (task/session/manifest-bound), a deny-by-default execution
policy, the Research Agent executor adapter, and the Codex CLI preflight +
sandboxed executor with a deterministic fake coding executor and a
controlled package installer.

**Not implemented in this phase** (explicitly out of scope): the final
top-level owner-handoff orchestrator, `run_owner_handoff_demo.py`,
`demo_workspace/`, the return coordinator and resume UI, real BLE, real
radar serial integration, real OCR, Dashboard/summary integration,
automatic macOS notifications, and any real external action (Git push,
publication, deploy, message/email sending). **No real Codex task was
executed** at any point in this session — preflight research used only
`codex --version` and `codex exec --help` (both inert, read-only, and never
contact a model service), and every executor test uses a fake process
runner/client.

- Branch: `feature/owner-authorized-handoff-v2` (unchanged base; not
  switched, not merged/rebased/committed/pushed).
- Report date: 2026-07-25.
- Starting state verified clean: `git status` showed only the expected
  uncommitted Phase 0-2 files before any change in this turn.

---

## Part A — Phase 2 repair gate

All seven defects were fixed and verified with a full green test run
(`python -B -m unittest discover -s tests -v` → 242 tests, 241 passed, 1
skipped, 0 failed) **before** any Phase 3 file was created.

### 1. Wearable authorization was forgeable and not retry-safe

The previous design exposed a public `advance_state_machine_for_answer(store,
task_id, question, answer, outcome)` that blindly trusted a caller-supplied
`outcome` object — anyone could construct `AnswerOutcome(accepted=True)`
directly and hand it in, with no real validation ever having occurred.
Fixed: that function is removed. `QuestionLifecycle.submit_answer` is now
the *one* authoritative path from a raw `WearableAnswer` to a state
transition — it performs its own internal validation (`_validate`, formerly
`evaluate_answer`'s body) and accepts no externally-supplied "already
validated" flag. `evaluate_answer` still exists as a non-authoritative,
side-effect-free dry-run check, clearly documented as carrying no authority.

- **Consumed only after success:** an answer is added to
  `_accepted_answers` only after `store.apply_transition` has actually
  succeeded — never before, never speculatively.
- **Idempotent replay:** resubmitting the *exact same* already-accepted
  answer returns the cached result again without a second transition
  attempt; a materially *different* answer for an already-answered question
  is rejected as `DUPLICATE`.
- **Retry-safe on failure:** if the store transition itself raises (e.g.
  `StaleTransitionError`), the answer is not marked consumed, so a
  legitimate later retry remains possible — verified directly in
  `tests/test_wearable_contract.py::QuestionLifecycleRetrySafetyTest`.
- **Rejected answers never mutate anything** — verified directly.
- **`start_question` now rejects overwriting a still-pending, unexpired
  question** (new `QuestionAlreadyPendingError`) — a caller must explicitly
  cancel or wait for expiry first.
- **Option-not-offered enforcement** (already present) continues to
  restrict accepted buttons to an option the specific question actually
  offers.

### 2. `PermissionQuestion`, a real, distinct domain model

Permission questions (CODEX_DATA/PACKAGE_INSTALL) were never representable
as `HandoffQuestion` before this repair. Added `domain/question.py`'s
`PermissionQuestion`: `question_id`, `permission_kind`, `context_summary`,
`prompt`, `created_at`/`expires_at`, and (required only for
PACKAGE_INSTALL, forbidden for CODEX_DATA) `package_names`/`proposed_argv`.
Options are always exactly `{"YES": "Yes", "NO": "No"}` — never A/B/C/D.
Both `context_summary` and `prompt` (and every package name/argv item) are
independently validated for unsafe content (full URLs, credential-like
strings) the same way `WorkContext`/`Evidence`/`HandoffQuestion` already
were.

### 3. Disconnected wearable behavior

- `WearableSimulator.read_proximity()` previously kept returning whatever
  proximity state was last set even while disconnected (e.g. stale
  `OWNER_AWAY`). Fixed: it now reads as `UNKNOWN` whenever disconnected.
- `disconnect()` now clears any already-queued answers — a pre-disconnect
  queued answer can never become deliverable after a later `reconnect()`.
- `send_question()` now validates its `letters` argument: non-empty, no
  duplicates, and drawn entirely from exactly one button family
  (`{A,B,C,D}` or `{YES,NO}`) — never mixed, never an arbitrary string.

### 4. Leave confirmation was not one-shot, and idle timers started at "forever idle"

Two distinct defects in `fusion/leave_detector.py`:

- **Not one-shot:** every tick past the 10-second mark re-reported
  `confirmed=True` for the same episode, not just the first crossing tick.
  Fixed with a per-episode `_episode_confirmed` guard: only the first tick
  that crosses the threshold reports `confirmed=True`; every later tick of
  the same episode reports `confirmed=False` even though the window has
  objectively been open long enough.
- **Idle timers defaulted to "forever idle":** `_last_mouse_activity_at`/
  `_last_keyboard_activity_at` started as `None`, and `_is_idle` treated
  `None` as idle — meaning a freshly constructed detector with radar/
  wearable already absent could confirm a leave almost immediately,
  skipping the 5-second idle wait entirely. Fixed: both timers now
  initialize to the construction-time monotonic value (or an explicit
  `initial_activity_at` the caller supplies), so a fresh process must still
  wait the full `input_idle_threshold_seconds` before "idle" becomes true.

New regression tests (`tests/test_presence_fusion.py`) cover: a fresh
detector with radar/wearable already absent still waits the full 5s+10s;
an explicit `initial_activity_at` is honored; only the first
threshold-crossing tick reports confirmed, verified both at the detector
level and end-to-end through the store (a second, non-confirming tick
produces no new history row, and even replaying the *first*, already-applied
evaluation again remains idempotent via the store's own event-id check).

### 5. Remaining question privacy gaps

`PermissionQuestion`'s `prompt`, `context_summary`, `package_names`, and
`proposed_argv` are all validated against the same unsafe-content check
`HandoffQuestion` already used — closing the gap the new domain model would
otherwise have reintroduced. (`Terminal` rendering itself was already safe
by construction in Phase 2: it only ever prints already-validated question
fields.)

### 6. Impossible answer timestamps

Added a documented, small clock-skew tolerance
(`CLOCK_SKEW_TOLERANCE_SECONDS = 5.0`) and a new `AnswerRejectionReason.
FUTURE_TIMESTAMP`: an answer timestamped more than 5 seconds ahead of this
process's own clock is rejected, even if it is still technically before the
question's expiry. Combined with the pre-existing `BEFORE_CREATION`/
`EXPIRED` checks, an answer timestamped before creation, at/after
expiration, or materially in the future are all rejected.

### 7. Cache cleanup

All `__pycache__` directories under `application/owner_handoff/` were
removed (all gitignored/untracked — confirmed via `git status --ignored`
before deletion). Pycache files for the newly-created test modules were
removed individually, each re-confirmed as ignored (`!!`) immediately
before deletion. **No historical tracked MVP `.pyc` file was touched** —
one was accidentally caught by an overly broad `find -delete` during this
work and was immediately restored via `git checkout --` before proceeding;
every subsequent cleanup command was scoped to specific, pre-verified-ignored
paths only. `python -B` was used for every test run from this point forward.

**Full repair-gate test run:** `python -B -m unittest discover -s tests -v`
→ **242 tests, 241 passed, 1 skipped, 0 failed**. Phase 3 work did not
begin until this was green.

---

## Part B — Phase 3 implementation

### Files changed

**New:**

```
application/owner_handoff/domain/execution.py
application/owner_handoff/domain/manifest.py
application/owner_handoff/routing/__init__.py
application/owner_handoff/routing/skills.py
application/owner_handoff/workspace/__init__.py
application/owner_handoff/workspace/path_policy.py
application/owner_handoff/workspace/duplicator.py
application/owner_handoff/execution/__init__.py
application/owner_handoff/execution/policy.py
application/owner_handoff/execution/permission.py
application/owner_handoff/execution/research_executor.py
application/owner_handoff/execution/coding_executor.py
application/owner_handoff/execution/codex_cli.py
application/owner_handoff/execution/package_installer.py
tests/test_skill_routing.py
tests/test_path_policy.py
tests/test_workspace_duplicator.py
tests/test_execution_policy.py
tests/test_permission_flow.py
tests/test_research_executor.py
tests/test_codex_executor.py
docs/AI_DESK_V2_PHASE3_REPORT.md
```

**Modified** (repair gate and/or Phase 3 integration — all within the
allowed list):

```
application/owner_handoff/fusion/leave_detector.py   - one-shot confirmation,
                                                        idle-timer init fix
                                                        (repair 4)
application/owner_handoff/adapters/wearable.py         - disconnect/letter
                                                        validation fixes
                                                        (repair 3)
application/owner_handoff/domain/question.py            - PermissionQuestion
                                                        added (repair 2)
application/owner_handoff/questions/lifecycle.py         - submit_answer as
                                                        the sole authoritative
                                                        path; clock-skew
                                                        check; permission-kind
                                                        transitions (repairs
                                                        1, 6; Phase 3
                                                        integration)
application/owner_handoff/README.md                      - repair corrections
                                                        + full Phase 3
                                                        documentation
tests/test_presence_fusion.py                             - repair 4
                                                        regression tests
tests/test_wearable_contract.py                            - repairs 1, 2, 3,
                                                        6 regression tests
tests/test_handoff_questions.py                             - updated to use
                                                        submit_answer instead
                                                        of the removed
                                                        advance_state_machine_
                                                        for_answer
README.md                                                    - Phase 3 summary
```

**Not modified:** `requirements.txt` (no new dependency — every Phase 3
module uses only the standard library plus existing project modules),
`.env.example` (no new `OwnerHandoffConfig` field was added in Phase 3; see
"Configuration" below), every existing legacy test module, every existing
application/system-layer module outside `application/owner_handoff/`, and
`application/handoff/` (untouched — `ResearchAgentExecutor` wraps it from
the outside).

No file outside the allowed lists (repair gate or Phase 3) was touched;
nothing required stepping outside them, so no stop-and-explain was needed.

### Architecture implemented

- **Skill routing** (`routing/skills.py`): `classify_goal_text` requires
  both a verb and a supporting object from the same category
  (coding/research); conflicting or ambiguous text fails closed to
  `SkillKind.UNSUPPORTED`. `route_handoff_question` builds a companion
  `HandoffPlan` (`domain/execution.py`) without ever changing
  `HandoffQuestion`'s own JSON contract.
- **Physical workspace duplication** (`workspace/`): `path_policy.py`
  validates only resolved, absolute paths (root/home/boundary/session-root/
  overlap/symlink/existing-destination rejection); `duplicator.py`'s
  `duplicate_workspace` performs the byte-copy with documented exclusions
  and inline pre/post-copy hash verification, and
  `verify_original_unchanged` independently re-hashes the original
  afterward — `original_files_modified` is only ever meaningful once
  `verification_performed` is `True` (`domain/manifest.py` enforces this at
  construction).
- **Permission flows** (`execution/permission.py`): `PermissionGate` binds
  a granted YES to the exact task, question, session, and
  manifest-hash (`compute_manifest_hash`) it was granted for —
  `verify_authorization` checks all four together, so a previous YES can
  never authorize a different task, duplicate, or modified manifest.
  `request_codex_data_permission`/`request_package_install_permission` wire
  the corresponding state-machine transitions.
- **Execution policy** (`execution/policy.py`): one shared, deny-by-default
  `ExecutionPolicy` consulted by both the Codex executor and the package
  installer before any runner is ever invoked.
- **Research Agent executor** (`execution/research_executor.py`):
  `ResearchAgentExecutor` wraps the existing, unmodified
  `A2AHandoffClient`/`TaskCapsule`, sending only the routed goal text.
- **Coding Agent executor** (`execution/coding_executor.py`,
  `execution/codex_cli.py`): the `CodingAgentExecutor` interface,
  `FakeCodingExecutor` for deterministic tests, and `CodexCLIExecutor` — a
  preflight-gated, sandboxed, JSONL-streaming real implementation with
  injectable command/process runners.
- **Package installer** (`execution/package_installer.py`): a strict
  `name`/`name==version` parser and an installer that only ever targets the
  duplicate-local virtual environment interpreter.

### Test results

```
python -B -m unittest discover -s tests -v
```

**Result: 337 tests run, 334 passed, 3 skipped, 0 failed.**

| Module | Tests |
|---|---|
| 11 legacy MVP modules + Phase 1/2 modules (242 total, unchanged from repair gate) | 242 (241 passed +1 skipped) |
| `test_skill_routing.py` | 11 |
| `test_path_policy.py` | 13 (1 skipped: symlink creation not permitted in this environment) |
| `test_workspace_duplicator.py` | 10 (1 skipped: same reason) |
| `test_execution_policy.py` | 15 |
| `test_permission_flow.py` | 19 |
| `test_research_executor.py` | 6 |
| `test_codex_executor.py` | 21 |
| **Total** | **337** (334 passed + 3 skipped) |

The two additional skips beyond the pre-existing OpenAI integration test are
both symlink-creation tests that gracefully skip in this Windows test
environment (creating a symlink requires elevated privileges/developer mode
here) rather than failing — the corresponding *non-symlink* rejection paths
(filesystem root, home, outside-boundary, overlap, existing destination) are
still fully covered.

No test calls a real Codex CLI, a real package installer, a real A2A
network endpoint, real BLE, real OCR, or a real subprocess of any kind —
every executor/installer/client boundary is injected with a fake in every
test.

### Safety decisions

- **Manifest verification is never falsely "clean."**
  `DuplicationManifest.__post_init__` refuses to construct a manifest that
  reports `original_files_modified`/`verification_passed` before
  `verification_performed` is `True` — there is no code path that can
  produce an empty `original_files_modified` without `verify_original_unchanged`
  having actually run and found nothing changed.
- **Permission binding is four-way, not just "was there a YES."**
  `PermissionGate.verify_authorization` requires task id, permission kind,
  session id, and manifest hash to all match — any one difference (a
  different task, a different duplicate, or a duplicate that changed after
  the grant) invalidates the authorization.
- **Codex CLI never starts without a real preflight pass.** Missing binary,
  missing required capability, or any nonzero preflight exit all raise
  `CodexPreflightError` before the process runner is ever invoked (verified:
  call count remains zero).
- **No dangerous flag can reach the Codex CLI invocation**, checked twice:
  the fixed argv construction never includes one, and `ExecutionPolicy`
  independently rejects a defense-in-depth test that force-injects one.
- **JSONL processing fails closed**: a single malformed line, an `error`/
  `*.failed` event, a nonzero exit code, or a stream that never produces a
  recognizable completion event all produce `FAILED` — never a
  best-effort "probably fine."
- **The package installer accepts nothing pip itself would accept more
  broadly** — `parse_package_spec` is a strict allowlist-style parser
  (`name` or `name==version` only), not a denylist of known-bad patterns.

### Dependency and interface confirmation

- `requirements.txt` is unchanged.
- No existing public API outside `application/owner_handoff/` was touched;
  `application/handoff/` (the existing Research Handoff Agent) is
  completely untouched — `ResearchAgentExecutor` only imports its existing
  `TaskCapsule`/`A2AResult` types and calls `send_task` on an injected
  client.
- No existing (legacy) test file was modified.
- No real Codex task was executed, no real package was installed, no real
  A2A/network request occurred, and no original workspace file was modified
  by anything in this phase's own test suite — confirmed by code review (no
  `subprocess`/`socket` import anywhere outside the documented, injectable
  runner *protocols*, which every test replaces with a fake) and by the
  workspace duplicator's own tamper-detection tests passing.

### Configuration

No new `OwnerHandoffConfig` field was added in Phase 3 — `CodexCLIExecutor`,
`PackageInstaller`, and `ResearchAgentExecutor` all take their configurable
values (binary name, runtime/step caps, platform) as constructor parameters
supplied by whatever wires them together, rather than reading `config.py`
directly. `coding_agent_max_runtime_seconds`/`coding_agent_max_safe_steps`
(defined in Phase 1) are the values a future integration point should pass
into `CodingTaskRequest`; `workspace_session_root` (also Phase 1) is the
value a future integration point should pass into `duplicate_workspace`.
`.env.example` is therefore unchanged — adding a variable with no code path
that reads it would be inaccurate documentation.

### Explicitly deferred (unchanged from, or newly clarified since, Phase 2)

The final top-level owner-handoff orchestrator; `run_owner_handoff_demo.py`;
`demo_workspace/`; the return coordinator and resume UI (a confirmed return
is still not wired into `EXECUTING -> RETURN_REQUESTED`); real BLE and all
BLE UUIDs; real radar serial integration; real OCR; Dashboard/summary
integration; automatic macOS notifications; Git push, publication, deploy,
or email/message sending (always denied by `ExecutionPolicy`, never
implemented); and any real Codex task execution, real package installation,
or real A2A network call in this or any prior phase's test suite.

### Known limitations

- Codex JSONL post-hoc inspection is a defense-in-depth signal, not a proof
  against a hostile or compromised model — documented plainly in
  `execution/codex_cli.py`'s module docstring and in
  `application/owner_handoff/README.md`, not hidden.
- The assumed Codex CLI JSONL event schema (`type`, `id`/`item_id`,
  `*.completed`/`*.succeeded` suffixes, `error`/`*.failed` for failures) is
  a documented, reasonable approximation for this MVP; it has not been
  verified against a real Codex CLI's actual output in this session (no
  real `codex exec` was ever run).
- `CodexPreflight`'s capability detection is substring matching against
  `--help` text for a bounded, documented flag list — not an exhaustive
  parse of every flag the CLI supports.
- Real symlink-rejection behavior could not be exercised on this Windows
  test environment without elevated privileges; the two affected tests
  skip gracefully rather than failing, and the equivalent non-symlink
  rejection paths remain fully tested.
- No macOS hardware verification has been performed for anything in this
  phase or any prior phase.

### Recommended Phase 4 scope

1. The return coordinator: wire `ReturnDetector`'s confirmed return into
   `EXECUTING -> RETURN_REQUESTED`, finish only the current atomic step,
   and build the resume report (Master Spec section 17).
2. The top-level orchestrator tying leave detection, question generation,
   routing, workspace duplication, permission flows, and execution together
   end to end.
3. `demo_workspace/` (a small, safe, illustrative fixture) and
   `run_owner_handoff_demo.py`.
4. A one-time, carefully reviewed rehearsal with the real installed Codex
   CLI (`codex-cli 0.134.0` was the version verified via preflight-style
   commands during earlier research, but never asserted or hardcoded) and
   the demo fixture — only after human review, per the Master Spec's
   staged-rollout requirement.

---

## End-of-phase verification

- [x] Repair gate green before Phase 3 began: 242 tests, 241 passed, 1
      skipped, 0 failed.
- [x] Full suite after Phase 3: **337 tests, 334 passed, 3 skipped, 0
      failed** (`python -B -m unittest discover -s tests -v`).
- [x] `git diff --check` — ran, no whitespace errors (only a benign
      LF/CRLF line-ending notice, not a `--check` failure).
- [x] `git status --short` and `git status --short --ignored` both ran;
      only the files listed above appear modified/untracked, and every new
      `__pycache__` directory shows as `!!` ignored.
- [x] Every new (untracked) text file explicitly scanned for trailing
      whitespace with a line-by-line regex pass — none found.
- [x] All legacy tests still pass, unchanged, unmodified.
- [x] `requirements.txt` unchanged — no new dependency.
- [x] No real Codex task was run, no real package was installed, no real
      A2A/network request occurred, and no original workspace file was
      modified — verified by code review and by the duplicator's own
      tamper-detection tests.
- [x] No new tracked `.pyc`/`__pycache__` remains; the one historical
      tracked MVP `.pyc` file accidentally touched mid-cleanup was restored
      immediately (see Part A, item 7).
- [x] No database, screenshot, `.env`, duplicate workspace, or temporary
      artifact remains anywhere in the tree.
- [x] This report created.
- [x] No commit created.
- [x] No push performed.
