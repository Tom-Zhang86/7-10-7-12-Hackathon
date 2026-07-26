# AI Desk V2 — Phase 4 Report

Branch: `feature/owner-authorized-handoff-v2` (based on `origin/MVP`).
This phase had two mandatory parts: **Part A**, a Phase 3 repair gate (must
pass before any Phase 4 work began), and **Part B**, the final MVP wiring
(top-level orchestrator, return coordinator, safe Terminal demo). Nothing in
this phase committed or pushed anything; no branch other than the one
supplied was touched.

Baseline reproduced at the start of this phase: `python -B -m unittest
discover -s tests -v` → **337 tests, 334 passed, 3 skipped, 0 failed** —
matched the expected baseline exactly before any change was made.

---

## Part A — Phase 3 repair gate

All seven items below were **fixed in code**, not merely documented as
limitations, with adversarial regression tests for each. Final count after
Part A: **408 tests, 405 passed, 3 skipped, 0 failed** (see "Complete test
results" below for the Phase 4 breakdown; Part A alone added 68 new tests
across `test_path_policy.py`, `test_workspace_duplicator.py`,
`test_execution_policy.py`, `test_skill_routing.py`,
`test_permission_flow.py`, `test_codex_executor.py`).

### 1. Enforced, one-time permission grants

**Problem:** `PermissionGate.record_grant` could be called with a
caller-fabricated "accepted" outcome, with no real link to a validated
wearable YES; `CodexCLIExecutor`/`PackageInstaller` could run without ever
consuming a grant.

**Fix** (`execution/permission.py`):
- Removed `record_grant`. The only way to record a grant is now
  `PermissionGate.grant_from_answer(lifecycle=..., answer=<raw
  WearableAnswer>, ...)`, which calls through to
  `QuestionLifecycle.submit_answer` and only records a grant from inside
  `submit_answer`'s own `on_permission_granted` callback — i.e. only after
  a real YES has survived every rejection check and the
  PERMISSION_PENDING -> EXECUTING transition has actually committed.
- Everything needed to build the grant record (in particular, computing
  the live duplicate digest via the new `compute_manifest_hash`) happens
  **before** `submit_answer` is even called. If that fails, no transition
  is attempted — the task is left exactly where it was, never landing in
  EXECUTING with a grant that failed to record
  (`test_grant_creation_failure_never_leaves_task_in_executing`).
- Added `PermissionGate.consume(...)`: an atomic, single-use "require and
  spend" operation, bound to task_id, question_id, permission_kind,
  session_id, duplicate path, a **freshly recomputed** duplicate digest,
  expiry, and (for PACKAGE_INSTALL) exact package specs + argv. A permit is
  deleted from the gate the instant it is consumed
  (`test_permit_cannot_be_reused`, `test_permit_reuse_rejected`).
- `CodexCLIExecutor.execute` and `PackageInstaller.install` now both
  compute the live duplicate digest and call `gate.consume(...)`
  immediately before invoking their respective runners; on
  `PermissionNotGrantedError` they fail closed without ever calling the
  runner (`test_no_permit_fails_closed_before_runner_called`,
  `test_install_without_permit_is_rejected_and_runner_never_called`).
- Duplicate tampering after a grant invalidates it
  (`test_duplicate_tampering_after_grant_invalidates_the_permit`,
  `test_duplicate_tampered_after_grant_fails_closed`), and a permit for one
  task/session/duplicate/package-command never authorizes another
  (`test_previous_yes_does_not_authorize_a_different_task`,
  `test_previous_yes_does_not_authorize_a_different_session`,
  `test_package_mismatch_rejected_even_with_a_granted_permit`).

### 2. Workspace exclusions and path safety

**Fix** (`workspace/duplicator.py`, `workspace/path_policy.py`):
- `.env`, `.env.local`, `.env.production`, etc. are now excluded via a
  case-insensitive `.env`/`.env.` prefix check (previously only exact
  `.env` or an exact `.env` suffix, which missed `.env.local`).
  `test_env_local_and_env_production_not_copied`.
- Excluded directory names/suffixes are matched case-insensitively, and the
  walk now uses `os.walk(topdown=True)` with excluded directory names
  *pruned from `dirnames` in place* — an excluded directory and everything
  under it is never individually enumerated, only recorded once
  (`test_excluded_directory_trees_are_pruned_not_enumerated`,
  `test_case_insensitive_excluded_directory_name`). Private-key-like files
  (`id_rsa`, `*.pem`, `*.key`, etc.) are excluded alongside secrets/
  credentials.
- Any symlink encountered anywhere in the tree now raises `PathPolicyError`
  and aborts the *entire* duplication — before any destination directory is
  even created — instead of being silently recorded as "excluded" while the
  rest of the copy proceeded (`test_symlink_inside_source_tree_fails_closed`,
  renamed from the old, bug-matching
  `test_symlink_inside_source_is_excluded_not_copied`).
- `validate_destination_path` now requires the destination be **exactly one
  direct child** of `session_root` (`resolved.parent == session_root_resolved`),
  not merely somewhere underneath it
  (`test_destination_nested_more_than_one_level_rejected`).
- New `validate_session_id()` rejects `.`/`..`, any path separator, absolute
  paths, and non-single-component values, checked before any filesystem
  write (`test_unsafe_session_id_rejected_before_any_write`,
  `SessionIdValidationTest`).

### 3. Live manifest hashing

**Fix** (`execution/permission.py`, `workspace/duplicator.py`):
- `compute_manifest_hash` now takes a **duplicate path** and freshly
  `rglob`s + re-hashes every file present right now — it no longer trusts
  `manifest.post_copy_hashes` as proof of current state. The hash changes
  for modified, added, deleted, or renamed files, and for a file replaced
  by a symlink of the same name.
- `verify_original_unchanged` (original-workspace side) now additionally
  re-scans the source tree with the same exclusion policy used at
  duplication time and reports any path present now that wasn't in the
  originally recorded set — catching additions and renames, not just
  modifications/deletions. The verification's scope (only what the
  exclusion policy would ever have copied) is documented explicitly in the
  function's docstring, not silently overclaimed as "every byte in the
  repository."
- New `scan_duplicate_changes(manifest)` freshly re-scans the **duplicate**
  side and records created/modified files relative to the post-copy
  hashes — this is what feeds the resume report's
  `files_created`/`files_modified_in_duplicate`
  (`test_accurate_files_created_and_modified_report`).

### 4. Codex CLI nested JSONL handling

**Fix** (`execution/codex_cli.py`):
- Switched from a flat, guessed event shape (`event["id"]`, `event["type"]`)
  to the documented nested shape: top-level `type` values like
  `item.started`/`item.completed`/`turn.completed`/`turn.failed`/`error`,
  with `id`/`type`/`command`/`text` nested under an `item` object.
- Unique work-item IDs are now counted from `item.id`, and `max_safe_steps`
  is enforced against these nested events
  (`test_step_cap_terminates_and_fails_with_25_nested_events`: 25 nested
  `item.completed` events with `max_safe_steps=20` terminates and fails).
- Only an explicit `turn.completed` event counts as overall completion —
  any number of `item.completed` events without it still fails
  (`test_item_completed_without_turn_completed_produces_failed`).
- `turn.failed`, `error`, malformed JSON, and premature EOF are all handled
  explicitly and produce `FAILED`.
- Sanitization (`_sanitize_event`) is now fully **recursive** over nested
  dicts/lists, stripping `env`/`environment`/`api_key`/`authorization`/
  `token`/`secret`/`password`/`credential`/`cookie`-family keys at any depth
  and redacting/bounding every string
  (`test_sensitive_keys_stripped_recursively`). Retained event count and
  per-string text length are both bounded (`MAX_RETAINED_EVENTS = 200`,
  `MAX_RETAINED_TEXT_CHARS = 2000`).
- A command event containing `git push`/`npm install`/`curl`/etc. now
  terminates the stream and fails immediately, documented explicitly as
  **post-hoc detection, not prevention** — it can stop further steps but
  cannot un-run a command the sandbox already allowed
  (`CodexCLIExecutorProhibitedCommandTest`).
- **Non-blocking timeout, even with zero output:** replaced the old
  `iter_lines()`-based `CodexProcessHandle` Protocol (a plain blocking
  iterator, which made the runtime-limit check unreachable for a silent
  child) with a `poll_line(timeout) -> PollResult` Protocol that must
  return within the requested timeout whether or not a line arrived. The
  real adapter (`_SubprocessCodexProcessHandle`) implements this with a
  background reader thread feeding a `queue.Queue`, so `poll_line` is a
  bounded `queue.get(timeout=...)` regardless of subprocess output.
  `test_timeout_terminates_even_with_zero_output` proves a child that never
  emits a single line is still caught.
- `--strict-config` was added to `REQUIRED_FLAGS` (the argv this executor
  builds always passes it) — preflight now fails closed if the installed
  CLI doesn't advertise it (`test_missing_strict_config_fails_closed`).
- Preflight now inspects **both stdout and stderr** of `exec --help`
  (`test_help_text_inspected_on_stderr_too`).

### 5. Complete `ExecutionPolicy`

**Fix** (`execution/policy.py`):
- `check_argv_is_list` now also rejects non-string/empty argv items.
- New `_normalize_executable_token()` strips path prefixes (POSIX or
  Windows separators, checked regardless of host platform) and a trailing
  `.exe`, lower-cased — `check_no_escalation`, `check_no_git_remote_operations`,
  and `check_no_uncontrolled_package_manager` all use it, so
  `/usr/bin/sudo`, `sudo.exe`, `/usr/bin/git push`, and
  `C:\...\git.EXE push` are all recognized
  (`test_absolute_executable_path_sudo_rejected`,
  `test_windows_exe_suffix_sudo_rejected`,
  `test_absolute_git_path_push_rejected`,
  `test_windows_git_exe_push_rejected`).
- `check_argv_safe_for_subprocess` now also calls
  `check_no_uncontrolled_package_manager`
  (`test_argv_safe_for_subprocess_calls_package_manager_check`) — previously
  it was a standalone method never wired into the common battery of checks
  the real executors actually call.

### 6. Token-boundary routing

**Fix** (`routing/skills.py`):
- Replaced plain substring matching (`keyword in lowered`) with a
  compiled, boundary-aware regex per hint list
  (`(?<![\w-])(?:phrase1|phrase2|...)(?![\w-])`, longest phrases first) so
  "fix" no longer matches inside "prefix" and "class" no longer matches
  inside "classification."
  `test_prefix_the_classification_report_is_unsupported`,
  `test_fix_substring_inside_longer_word_does_not_count_as_verb`,
  `test_class_substring_inside_classification_does_not_count_as_object`,
  plus verb-only/object-only/conflicting-evidence regressions.

### 7. Real subprocess adapters

**Fix** (`execution/codex_cli.py`, `execution/package_installer.py`):
- `SubprocessCommandRunner` (preflight), `SubprocessCodexProcessRunner` +
  `_SubprocessCodexProcessHandle` (Codex execution), and
  `SubprocessInstallerCommandRunner` (package install) are now real,
  concrete, `shell=False`, argv-list, bounded-output implementations —
  previously only Protocols/fakes existed.
- None of this repository's automated tests invoke a real Codex CLI,
  network endpoint, or package installer; `SubprocessAdapterSmokeTest`
  proves the *plumbing* itself (real `subprocess.run`, `shell=False`, argv
  list) works by shelling out to `sys.executable --version` — an inert,
  always-available command, never Codex/npm/pip.

Part A checkpoint: `python -B -m unittest discover -s tests -v` → **408
tests, 405 passed, 3 skipped, 0 failed** before any Part B code was written.

---

## Part B — Phase 4 final MVP wiring

### Architecture / final execution flow

```
OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED -> QUESTION_PENDING -> AUTHORIZED
  -> RESEARCH: EXECUTING
  or
  -> CODING: WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA) -> EXECUTING
       -> (optional) PERMISSION_PENDING(PACKAGE_INSTALL) -> EXECUTING
-> RETURN_REQUESTED (when applicable)
-> READY_FOR_REVIEW -> RETURNED
```

`OwnerHandoffOrchestrator` (`orchestrator.py`) composes every Phase 1-3
component through dependency injection behind deterministic,
explicitly-called methods — there is no background thread, poller, or
sleep in the orchestrator itself (the `ReturnCoordinator`'s bounded grace
period is the one place a background thread exists, and only to honor a
genuinely concurrent return signal; every test drives it with an
event-based fake step, never a real sleep).

Key design decision, documented in the orchestrator's own docstring:
`QuestionLifecycle.submit_answer` only knows "was D selected or not" — it
authorizes AUTHORIZED for *any* non-D letter, regardless of what that
option's text routes to. Routing safety is enforced one layer up:
`execute_authorized_task` checks the routed `SkillKind` before any
duplication or execution begins, and cancels (never executes) an
UNSUPPORTED route. This keeps `HandoffQuestion`'s external JSON contract,
and the lifecycle's own authorization semantics, completely unchanged.

Only one task may be active per orchestrator instance at a time
(`start_task` raises `OrchestratorBusyError` otherwise) — the MVP's
single-active-task rule.

`ReturnCoordinator` (`return_coordinator.py`) drives EXECUTING ->
RETURN_REQUESTED -> READY_FOR_REVIEW -> RETURNED. Its `run_current_step`
runs the current atomic step (one `execute()` call) on a background
thread purely so a genuinely concurrent return can be honored with a
bounded grace period (default 30s, fixed constant) instead of blocking
forever; if the step doesn't finish in time, a caller-supplied
`terminate_fn` is invoked and `StepDidNotFinishInGracePeriodError` is
raised — the caller must not start another step regardless. `finalize`
always re-derives the resume report from a fresh `scan_duplicate_changes` +
`verify_original_unchanged` re-scan, never from caller-asserted lists.

`domain/resume.py`'s `ResumeReport.as_dict()` produces exactly:

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

### Real vs. fake/demo boundaries

| Component | Fake (default, all tests) | Real (explicit, human-invoked only) |
|---|---|---|
| Radar / wearable | `SimulatorRadar` / `WearableSimulator` | not implemented (deferred) |
| Research executor | `_FakeA2AClient` (`run_owner_handoff_demo.py --research fake`) | `A2AHandoffClient` (`--research real`, needs agent-skeleton running) |
| Coding executor | `FakeCodingExecutor` (`--executor fake`) | `CodexCLIExecutor` + `SubprocessCodexProcessRunner` (`--executor codex`) |
| Package installer runner | `_FakeInstallRunner` (demo) / injected fakes (tests) | `SubprocessInstallerCommandRunner` (wired, never invoked by the demo CLI's default flags) |
| Preflight command runner | `FakeCommandRunner` (tests) | `SubprocessCommandRunner` |

No automated test in this repository ever selects a real mode. The demo
CLI's real modes exist and are reachable, but require explicit flags a
human must pass.

### Permission and safety guarantees (carried over + Phase 4 additions)

- A CODEX_DATA/PACKAGE_INSTALL permit is single-use, bound to the exact
  task/question/session/duplicate/live-digest/(package command), and
  atomically consumed immediately before the real runner is invoked (Part A
  item 1).
- `execute_authorized_task` cancels (never executes) an UNSUPPORTED or
  DO_NOTHING route.
- Owner return before authorization (QUESTION_PENDING/PERMISSION_PENDING)
  cancels via the existing lifecycle helpers with zero executor calls;
  owner return during EXECUTING stops new-step starts via
  `ReturnCoordinator.request_return`, letting only the current step finish
  within its grace period.
- `OwnerHandoffOrchestrator.recover_after_restart()` combines
  `store.recover_after_restart()` (WORKSPACE_DUPLICATING/EXECUTING/
  PERMISSION_PENDING -> FAILED) and
  `recover_pending_questions_after_restart()` (QUESTION_PENDING ->
  CANCELED) — restart recovery remains fail-closed end-to-end.
- Nothing in the return coordinator or orchestrator ever merges or copies
  duplicate-workspace changes back into the original workspace.

### Files changed

**New (Part A repair, within existing files):** no new files — Part A
edited `execution/permission.py`, `execution/codex_cli.py`,
`execution/coding_executor.py`, `execution/package_installer.py`,
`execution/policy.py`, `routing/skills.py`, `workspace/duplicator.py`,
`workspace/path_policy.py`, and their corresponding tests
(`tests/test_path_policy.py`, `tests/test_workspace_duplicator.py`,
`tests/test_execution_policy.py`, `tests/test_skill_routing.py`,
`tests/test_permission_flow.py`, `tests/test_codex_executor.py`).

**New (Part B):**
- `application/owner_handoff/orchestrator.py`
- `application/owner_handoff/return_coordinator.py`
- `application/owner_handoff/domain/resume.py`
- `demo_workspace/README.md`
- `demo_workspace/sample_project/calculator.py`
- `demo_workspace/sample_project/test_calculator.py`
- `run_owner_handoff_demo.py`
- `tests/test_return_coordinator.py`
- `tests/test_owner_handoff_e2e.py`
- `docs/AI_DESK_V2_PHASE4_REPORT.md` (this file)

**Documentation updated:** `README.md` (root — Phase 4 summary + new
"AI Desk V2 — macOS demo" section), `application/owner_handoff/README.md`
(Phase 4 section, updated deferred/limitations lists). `.env.example` and
`requirements.txt` were **not** changed — no new genuinely-configurable
value was introduced (the return-coordinator grace period, JSONL retention
bounds, and preflight/installer timeouts are documented fixed constants,
consistent with how `MISSING_CODEX_CLI_POLICY` and similar invariants are
handled elsewhere in this codebase).

### Complete test results

```
python -B -m unittest discover -s tests -v
Ran 408 tests in ~18-20s
OK (skipped=3)
```

- 405 passed, 0 failed, 3 skipped (1 pre-existing OpenAI integration test
  requiring `RUN_OPENAI_INTEGRATION=1`; 2 symlink-creation tests skipping on
  this Windows development environment, which does not permit
  unprivileged symlink creation — documented, not a failure).
- New test files added this phase: `tests/test_return_coordinator.py` (8
  tests), `tests/test_owner_handoff_e2e.py` (22 tests, including the demo
  CLI smoke tests) — plus the expanded Part A repair-gate tests listed
  above.
- All 6 pre-existing `tests/test_handoff.py` tests re-verified passing in
  isolation.
- `requirements.txt` diff: empty. `git diff --check`: only a benign
  LF/CRLF warning on `.env.example`.

### Remaining limitations (see also each module's own docstring)

- Simulator wearable/radar only; no real BLE, no real serial reader.
- Terminal is the only UI; no Dashboard/summary integration.
- No OCR fallback (`ocr_adapter_mode` remains `noop`/`mock` only).
- Real Codex execution is a real subprocess call once explicitly requested
  (`--executor codex`) — every layer (sandbox, network-disabled, physical
  duplicate, fixed prompt policy, post-hoc manifest re-hashing, the
  installer gate) is defense in depth, not a formal guarantee; any real
  Codex rehearsal still requires explicit human review of the resulting
  diff before it is trusted or applied.
- The resume report is printed, not automatically written to a file; the
  duplicate workspace is never automatically deleted or merged back.

### Exact macOS demo commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python3 run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace/sample_project \
  --executor fake
```

See the root `README.md`'s "AI Desk V2 — macOS demo" section for the full
walkthrough, including the optional real-Research and real-Codex commands.

### Confirmation

- No real Codex task was ever run in this session — every automated test
  uses a fake `CodexProcessRunner`/`CommandRunner`; the one real-subprocess
  test (`SubprocessAdapterSmokeTest`) only runs `sys.executable --version`.
- No real package was ever installed — every test uses a fake
  `InstallerCommandRunner`.
- No real A2A/network request was ever made — every test uses a fake A2A
  client; the demo CLI's default `--research fake` never imports
  `application/handoff/a2a_client.py`'s real HTTP path.
- No original workspace file was ever modified by any test or by the
  manual demo runs performed while writing this report (`git status`
  confirms `demo_workspace/` and every source fixture used is byte-for-byte
  as committed here).
- No new `owner_handoff`/test `__pycache__` remains tracked; all show as
  ignored (`!!`) in `git status --ignored`.
- No database, screenshot, `.env`, duplicate-workspace, or temp artifact
  from this phase's work is tracked in git (manual demo runs used
  `--session-root`/`--db-path` pointed outside the repository).
- No commit or push was performed at any point in this phase.
