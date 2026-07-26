# AI Desk V2 — Phase 1 Report

Scope: centralized configuration, Phase 1 domain models, the persisted
state machine and transition-event ledger, activity/context classification,
the OCR interface boundary (no real OCR), and Phase 1 tests/documentation
only. **No radar/wearable adapters, no fusion, no question generator, no
executors, no workspace duplication, no orchestrator, no demo were
implemented — see "Deferred features" below.**

- Branch: `feature/owner-authorized-handoff-v2` (unchanged base; not
  switched, not merged/cherry-picked from `Friday`).
- Report date: 2026-07-25.

> **Phase 1 repair gate (2026-07-25, same-day follow-up review), applied
> before Phase 2 began:** six independently verified defects in this
> phase's implementation were fixed and are recorded in
> [`docs/AI_DESK_V2_PHASE2_REPORT.md`](AI_DESK_V2_PHASE2_REPORT.md) section
> 1 — context classification wrongly letting repeated communication/dual-use
> domains resolve `NOT_WORKING`; a `WorkContext` privacy bypass in the
> tracker's `append_*`/`set_project_and_task` helpers and in direct
> dataclass construction; missing permission-kind auditing when leaving
> `PERMISSION_PENDING`; event-replay conflict detection not comparing
> `reason`; restart-recovery event-id collisions across repeated task
> lifecycles; and two documentation contradictions (this section 5 below,
> and a since-corrected pycache-cleanup contradiction in the Phase 0
> Report). Every claim below that those repairs affect has been updated in
> place rather than left to silently go stale.

---

## 0. Phase 0 documentation corrections applied

Before any product code was written, the four approved review corrections
were recorded:

1. **Friday summary regression** — `docs/AI_DESK_V2_PHASE0_REPORT.md`
   section 3.3's "Summary regressions" row was corrected from "not a
   regression per se" to a confirmed regression. Independently re-verified
   in this session (a throwaway `git worktree` of `origin/Friday`, without
   switching this branch): Friday's own test suite produces **44 passed, 3
   failed, 1 skipped** (not the MVP baseline's 47/0/1). The three failures
   are documented with their exact test names and root causes.
2. **Codex data-authorization state order** — the Phase 0 Report's
   transition table (section 5) was corrected so Codex CLI cannot start
   before a separate, matching data-authorization YES. Corrected order:
   `AUTHORIZED -> WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA)
   -> EXECUTING`, with `EXECUTING -> PERMISSION_PENDING(PACKAGE_INSTALL)`
   as the only other valid route into a pending permission. Every
   `PERMISSION_PENDING` row now records an explicit `PermissionKind`.
3. **Safety invariants vs. configurable values** — a "Phase 1 Review
   Addendum" section was appended to `docs/AI_DESK_V2_MASTER_SPEC.md`
   (original sections 1–20 left untouched) distinguishing configurable
   values from seven mandatory safety invariants, and retracting four
   previously proposed environment variables
   (`AI_DESK_V2_WEARABLE_UNKNOWN_TRIGGERS`, `AI_DESK_V2_CONTRADICTION_POLICY`,
   `AI_DESK_V2_EXTERNAL_ACTIONS_ENABLED`, `AI_DESK_V2_OCR_POLICY`). The Phase
   0 Report's configuration table (section 7) was updated to match.
4. **Unresolved decisions resolved** — all seven items in the Phase 0
   Report's section 16 were marked resolved with the approved decisions
   (simulator-only radar in Phase 1 with a future single-serial-port-owner
   requirement; 5-second input idle threshold; Codex flag discovery
   deferred to Phase 3; BLE UUIDs still deferred; demo workspace deferred to
   Phase 4; Terminal-only display in Phase 1, no osascript notifier; do not
   remove pre-existing tracked MVP `.pyc` files). The checklist's dangling
   "§19" reference (this report has no section 19) was also corrected.

---

## 1. Files changed

**New files (all within the allowed Phase 1 list; none required stepping
outside it):**

```
application/owner_handoff/__init__.py
application/owner_handoff/config.py
application/owner_handoff/state_machine.py
application/owner_handoff/store.py
application/owner_handoff/domain/__init__.py
application/owner_handoff/domain/activity.py
application/owner_handoff/domain/work_context.py
application/owner_handoff/classification/__init__.py
application/owner_handoff/classification/activity_classifier.py
application/owner_handoff/classification/context_analyzer.py
application/owner_handoff/classification/ocr_policy.py
application/owner_handoff/adapters/__init__.py
application/owner_handoff/adapters/ocr.py
application/owner_handoff/README.md

tests/test_owner_handoff_config.py
tests/test_owner_handoff_state_machine.py
tests/test_activity_classifier.py
tests/test_context_analyzer.py
tests/test_work_context.py
tests/test_ocr_policy.py

docs/AI_DESK_V2_PHASE1_REPORT.md
```

**Existing files modified:**

```
docs/AI_DESK_V2_MASTER_SPEC.md      - appended "Phase 1 Review Addendum"
                                       (original sections 1-20 unchanged)
docs/AI_DESK_V2_PHASE0_REPORT.md     - four corrections applied (see section 0)
.env.example                          - appended AI_DESK_V2_* variables
README.md                             - appended a Phase 1 summary section
```

**Not modified:** `requirements.txt` (no new dependency was needed),
`.gitignore` (the existing `__pycache__/` rule already covers every new
module — verified with `git status --ignored`), every existing test module,
and every existing application/system-layer module.

No file outside the allowed list was created or modified. Nothing required
stepping outside the allowed-files list, so no stop-and-explain was needed.

---

## 2. Behavior implemented

- **Centralized configuration** (`config.py`): one immutable
  `OwnerHandoffConfig` dataclass, loaded via `load_owner_handoff_config()`,
  covering exactly the eight Phase 1 fields specified. Every override is
  validated (positive finite durations, positive step count, non-empty
  non-root paths, known OCR adapter mode); invalid values raise
  `ConfigError` immediately. Seven mandatory safety invariants are plain
  module constants, never read from the environment.
- **Domain models** (`domain/activity.py`, `domain/work_context.py`):
  `ActivityClassification`, `ActivitySample` (structurally incapable of
  holding key values, pointer-coordinate streams, full URLs, or screenshot
  bytes), `EvidenceKind`, `Evidence`, `WorkContext` (exact Master Spec
  section 5 JSON shape), and `WorkContextTracker` (dedup, bounded lists,
  corroboration-gated confidence and project/task assignment, conflict and
  staleness penalties, secret/URL redaction).
- **Persisted state machine** (`state_machine.py`, `store.py`): all 13
  Master Spec states, the corrected Codex-authorization transition order,
  `PermissionKind`-aware validation, and `OwnerHandoffStore` — a SQLite-
  backed, WAL-mode, atomic-compare-and-swap store with a real idempotent
  event ledger (verified replay-safe and conflict-detecting, not merely
  inferred from a failed compare-and-swap) and an explicit
  `recover_after_restart()` that force-fails runtime-bound tasks without
  ever triggering on mere import/construction.
- **Tier 1 activity classification** (`classification/activity_classifier.py`):
  deterministic, OCR-free, using only aggregate interaction counts and the
  active application name, with two small bounded hint lists (task
  surfaces; communication/entertainment surfaces) used only as hints, never
  as exhaustive "final truth."
- **Tier 2 context analysis** (`classification/context_analyzer.py`): runs
  only on Tier 1 `AMBIGUOUS`, requires either same-detail recurrence or
  two-distinct-source-type corroboration before resolving, never lets a
  weak entertainment mention overwrite strong task evidence, and treats a
  genuine same-strength conflict as `AMBIGUOUS`.
- **OCR boundary** (`adapters/ocr.py`, `classification/ocr_policy.py`):
  `OCRProvider` protocol, `NoOpOCRProvider`, `MockOCRProvider`, and a fixed
  Tier 1 -> Tier 2 -> OCR invocation function that calls OCR at most once,
  only when both tiers are `AMBIGUOUS`, and sanitizes any returned text
  before it is treated as evidence. No real OCR dependency of any kind is
  present.

---

## 3. Tests and exact counts

```
python -m unittest discover -s tests -v
```

**Result: 115 tests run, 114 passed, 1 skipped, 0 failed.**

- Pre-existing MVP baseline: **47 passed, 1 skipped** — unchanged, all 11
  existing test modules untouched and still passing.
- New Phase 1 tests: **67 passed**, across 6 modules:

| Module | Tests |
|---|---|
| `test_owner_handoff_config.py` | 11 |
| `test_activity_classifier.py` | 10 |
| `test_context_analyzer.py` | 6 |
| `test_work_context.py` | 16 |
| `test_ocr_policy.py` | 8 |
| `test_owner_handoff_state_machine.py` | 16 (one test carries 21 `subTest` cases covering every allowed transition individually) |
| **Total new** | **67** |

47 (legacy) + 67 (new) + 1 (skipped) = 115, matching the suite total
exactly. Every new public component has at least one normal-path test and
at least one rejection/error-path test; the state machine and store
additionally have explicit unauthorized/forbidden-path tests (forbidden
transitions, stale source state, event-ID conflict, and the Codex/package
permission-kind ordering checks).

No new test calls real network, hardware, OCR, Codex CLI, or a package
installer — `test_owner_handoff_config.py`'s safety-invariant test does pass
a fake environment mapping containing bogus keys, but only to prove those
keys are never read, not to exercise any real override path.

---

## 4. Deferred features (explicitly out of Phase 1 scope)

Not implemented — no code exists for any of the following, and nothing in
this phase's documentation claims otherwise:

- radar hardware adapter (simulator or real);
- wearable/BLE adapter, including any simulator;
- presence fusion (leave detection) and return fusion;
- the handoff-question generator and the interactive Terminal question UI;
- `ResearchAgentExecutor` and `CodingAgentExecutor`;
- Codex CLI integration of any kind (flag discovery is deferred to Phase 3
  per the resolved unresolved-decisions list; Phase 1 does not guess flags);
- physical workspace duplication and path-safety policy;
- the permission UI (Codex data-authorization / install-authorization
  prompts — only the state machine's transition rules for these exist);
- the top-level orchestrator and the return coordinator;
- `demo_workspace/` and `run_owner_handoff_demo.py` (contents deferred to
  Phase 4);
- Dashboard integration and any change to the existing daily-summary
  pipeline;
- real OCR of any kind (macOS Vision, Tesseract, cloud OCR — none present);
- any real network or hardware behavior.

**No macOS hardware verification has been performed for anything in this
phase.** Every Phase 1 test is deterministic, in-process, and runs on any
OS.

---

## 5. Safety invariants (Phase 1 status)

Centralized as plain constants in `application/owner_handoff/config.py`,
never environment-overridable, and covered by
`test_owner_handoff_config.py::OwnerHandoffSafetyInvariantTest`:

| Invariant | Phase 1 status |
|---|---|
| Wearable `UNKNOWN` never triggers | Constant defined (`WEARABLE_UNKNOWN_NEVER_TRIGGERS`); no wearable adapter exists yet to consume it |
| Contradictory signals always wait | Constant defined (`CONTRADICTORY_SIGNALS_POLICY`); no fusion exists yet to consume it |
| External actions denied in v1 | Constant defined (`EXTERNAL_ACTIONS_ENABLED = False`); no executor exists yet to consume it |
| OCR callable only after Tier 1 and Tier 2 both `AMBIGUOUS` | **Fully implemented and tested** (`resolve_with_ocr`) |
| Unanswered/timed-out question always resolves to D | Constant added by the repair gate (`UNANSWERED_QUESTION_DEFAULT = "D"`); no question generator/lifecycle exists yet to consume it |
| Missing Codex CLI always stops safely (not a configurable policy) | Constant added by the repair gate (`MISSING_CODEX_CLI_POLICY = "stop_safely"`); no Codex CLI integration exists yet to consume it |
| Missing Codex isolation always fails closed | Not yet applicable — no Codex CLI integration exists in Phase 1 |
| Original workspace never writable | Not yet applicable — no workspace duplication exists in Phase 1 |
| Git push/publish/message/deploy prohibited | Not yet applicable — no executor exists in Phase 1 |

**Repair gate correction:** the two new rows above did not exist as real
constants when this report was first written — the Phase 0 Report's
configuration table incorrectly listed `unanswered_question_default` and
`missing_codex_cli_policy` as ordinary, environment-overridable config
fields. Both are now corrected everywhere (Master Spec addendum, Phase 0
Report, this report, `application/owner_handoff/README.md`) to be
non-overridable invariants backed by real constants, matching how the other
five invariants in this table were already documented.

The invariants that have no Phase 1 consumer are recorded now specifically
so that when their consuming component is built in a later phase, it reads
from these existing constants rather than reintroducing a parallel,
possibly-overridable policy.

---

## 6. Unresolved issues

- **Codex CLI sandbox flags** remain unknown and unguessed, deferred to
  Phase 3 per the resolved decision — no code in this repository assumes
  any particular flag name.
- **BLE Service/Characteristic UUIDs, payload encoding, device
  provisioning, RSSI/proximity policy, reconnect behavior, replay
  protection, and button debounce behavior** remain fully deferred; no
  placeholder or guessed values exist anywhere in this phase.
- **Real radar serial ownership**: the future single-serial-port-owner/
  broker requirement (resolved decision) is documented but has no code yet
  to enforce it, since no radar adapter exists in Phase 1.
- **`demo_workspace/` contents** remain undecided (deferred to Phase 4).
- **Pre-existing tracked `.pyc` files on `origin/MVP`** were left untouched
  per the resolved decision; running the test suite regenerates their
  bytecode as a working-tree side effect, which was reverted with
  `git checkout --` before finishing this phase (twice, once per full test
  run) so the final diff contains only the documentation and `.env.example`
  changes plus the new files.

---

## 7. Phase 2 recommendations

1. Implement the radar and wearable adapters (simulator-first, per the
   resolved single-serial-port-owner requirement for any future real radar
   reader) and the presence/return fusion that consumes
   `input_idle_threshold_seconds` and `owner_leave_confirmation_seconds`.
2. Implement the handoff-question generator and Terminal question UI,
   consuming `WorkContext`/`WorkContextTracker` and
   `handoff_question_expiration_seconds` (defined but unconsumed in Phase 1).
3. Wire the state machine's `PERMISSION_PENDING(CODEX_DATA)` /
   `PERMISSION_PENDING(PACKAGE_INSTALL)` transitions to real permission-
   request/response handling once the wearable adapter exists.
4. Keep Codex CLI flag discovery scoped to Phase 3 as decided — do not pull
   it forward.
5. When building `ResearchAgentExecutor`/`CodingAgentExecutor`, consult
   `RUNTIME_BOUND_STATES`/`recover_after_restart()` from this phase's
   `store.py` rather than re-deriving restart-recovery logic independently.

---

## 8. End-of-phase verification

- [x] Full test suite run: **115 tests, 114 passed, 1 skipped, 0 failed**
      (`python -m unittest discover -s tests -v`).
- [x] `git diff --check` — ran, no whitespace errors (only a benign
      LF/CRLF line-ending notice on `.env.example`, not a `--check`
      failure).
- [x] All new (untracked) text files explicitly scanned for trailing
      whitespace with a line-by-line regex pass — none found.
- [x] `git status --short` — only the allowed Phase 1 files appear
      (`.env.example`, `README.md` modified; `application/owner_handoff/`,
      `docs/`, and the six new test modules untracked).
- [x] No new or pre-existing-but-now-tracked `__pycache__`/`.pyc`, database,
      screenshot, temporary OCR artifact, `.env`, or generated workspace —
      verified with `git status --ignored` (new `__pycache__` dirs under
      `application/owner_handoff/` show as `!!` ignored, not untracked) and
      a direct filesystem check (no `data/` or `demo_workspace/` directory
      exists).
- [x] Only the allowed Phase 1 files changed — confirmed against the exact
      allowed-files list; nothing required stepping outside it.
- [x] This report created.
- [x] No commit created.
- [x] No push performed.
- [x] Stopping here for human review.
