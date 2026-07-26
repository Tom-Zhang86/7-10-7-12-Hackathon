# AI Desk V2 — Master Spec

> Preserved verbatim as supplied for Phase 0 (repository audit and architecture
> design). This document is the source of truth for all safety and behavior
> requirements. Nothing in this file may be weakened, reinterpreted, or
> silently omitted by later phases. Any change to a decision below requires an
> explicit, recorded user instruction.

Base branch: `origin/MVP`. Working branch: `feature/owner-authorized-handoff-v2`.

---

## 1. Product definition

AI Desk V2 is an identity-aware, presence-triggered, user-authorized, sandboxed
agent handoff orchestrator for macOS.

It is not only a productivity tracker.

It must:

- observe aggregate computer activity;
- maintain a structured understanding of the user's current work;
- distinguish physical desk presence from owner proximity;
- confirm that the actual owner has left;
- generate a small context-specific multiple-choice question;
- receive explicit authorization through a wearable;
- route research work to the existing Research Agent;
- route small coding work to a separate Coding Agent using Codex CLI;
- physically duplicate the selected workspace;
- execute only inside the duplicate;
- stop safely when the owner returns;
- produce a structured resume report;
- prove that the original workspace was not modified.

## 2. Confirmed implementation decisions

These decisions are final unless the user explicitly changes them:

- Base branch: origin/MVP.
- New branch: feature/owner-authorized-handoff-v2.
- Friday must not be merged wholesale.
- Useful Friday ideas may be selectively ported only after review, repair,
  integration, and tests.
- Existing Research Handoff Agent remains a separate Research Agent.
- A new Coding Agent abstraction must be added.
- The real coding demo uses Codex CLI.
- Normal tests use a deterministic fake Coding Agent, never the real CLI.
- Workspaces must be physically copied.
- Git worktree is not acceptable for execution isolation.
- Wearable BLE support initially consists of:
  - interfaces;
  - message contracts;
  - simulator;
  - extensive code comments at the hardware boundary;
  - detailed README documentation.
- Do not invent final BLE UUIDs.
- The full handoff question is displayed in Terminal.
- The wearable returns only A/B/C/D or YES/NO with a question ID.
- OCR initially consists of:
  - an interface;
  - invocation policy;
  - no-op implementation;
  - mock implementation;
  - tests;
  - detailed README documentation.
- Do not implement real OCR in the first version.
- Supported task types:
  - research;
  - small coding changes.
- Research and coding must use separate skills and executors.
- Installation permission is answered through wearable YES/NO.
- External actions require separate authorization and are denied by default.
- Every public component must have simple relevant tests.
- All configurable or currently hard-coded parameters must appear in:
  - one centralized config module;
  - .env.example;
  - a README parameter table.

## 3. Default configuration

Use centralized configurable defaults:

- owner leave confirmation: 10 seconds;
- handoff question expiration: 60 seconds;
- unanswered question: D / Do nothing;
- Coding Agent maximum runtime: 300 seconds;
- Coding Agent maximum safe steps: 20;
- wearable UNKNOWN: never trigger;
- contradictory sensor signals: wait;
- missing Codex CLI: stop safely;
- missing hardware: simulator remains usable.

Do not scatter these values as magic numbers.

## 4. Activity classification

Observe:

- aggregate mouse activity;
- aggregate keyboard activity;
- interface switching;
- active application.

Never store actual keys.

Classify each observation period as exactly:

- WORKING;
- NOT_WORKING;
- AMBIGUOUS.

Rules:

- WORKING updates WorkContext.
- NOT_WORKING does not update WorkContext.
- AMBIGUOUS proceeds to additional context analysis.

Additional context may include:

- window title;
- active Chrome tab title and domain;
- visible filename;
- recent repeated evidence;
- currently known project and task.

Only if activity and metadata remain ambiguous may OCRFallback be invoked.

OCR requirements:

- never run continuously;
- never run for clear WORKING or NOT_WORKING cases;
- first version uses only mock/no-op adapters;
- future real OCR must use the minimum screen region;
- screenshots must not be persisted by default;
- temporary screenshots must be deleted immediately;
- only sanitized extracted text required for context may be retained.

## 5. Structured work context

Persist:

```json
{
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

Rules:

- use repeated or corroborating evidence;
- do not infer a project from one random keyword;
- distinguish observations from inference;
- deduplicate repeated evidence;
- reduce confidence when evidence conflicts or becomes stale;
- never store secrets, tokens, passwords, or raw private OCR content.

## 6. Hardware roles

Desk radar answers:

- PERSON_PRESENT;
- PERSON_ABSENT;
- UNKNOWN.

Wearable proximity answers:

- OWNER_NEAR;
- OWNER_AWAY;
- UNKNOWN.

Wearable buttons answer:

- A;
- B;
- C;
- D;
- YES;
- NO.

Wearable answers must include:

```json
{
  "device_id": "",
  "question_id": "",
  "button": "",
  "timestamp": ""
}
```

Reject unknown devices, stale question IDs, duplicate answers, expired
questions, and answers received in the wrong state.

The BLE implementation is deferred because final firmware details are not yet
available.

Code comments and README must document the future need for:

- BLE Service UUID;
- Characteristic UUIDs;
- payload encoding;
- device provisioning;
- RSSI/proximity policy;
- reconnect behavior;
- replay protection;
- button debounce behavior.

## 7. Leave detection

Do not trigger from one signal.

Confirm OWNER_LEFT_CONFIRMED only when all remain true for 10 seconds:

- radar == PERSON_ABSENT;
- wearable == OWNER_AWAY;
- mouse inactive;
- keyboard inactive.

If signals conflict or are UNKNOWN, wait.

Examples:

- radar present + wearable away:
  another person may be at the desk; do not act.

- radar absent + wearable near:
  owner may still be nearby; wait.

- radar absent + wearable away + active keyboard:
  do not act.

Use monotonic time internally and an injected clock in tests.

## 8. Persisted state machine

Design an explicit persisted state machine containing at least:

- OBSERVING;
- LEFT_CANDIDATE;
- OWNER_LEFT_CONFIRMED;
- QUESTION_PENDING;
- AUTHORIZED;
- WORKSPACE_DUPLICATING;
- EXECUTING;
- PERMISSION_PENDING;
- RETURN_REQUESTED;
- READY_FOR_REVIEW;
- RETURNED;
- CANCELED;
- FAILED.

Every transition must:

- define allowed source states;
- be idempotent;
- include timestamp and reason;
- reject stale events;
- survive a process restart where practical;
- ensure one absence event starts at most one task.

## 9. Handoff question

After OWNER_LEFT_CONFIRMED:

- freeze the latest WorkContext;
- generate two or three realistic next directions;
- add D = Do nothing;
- display the complete question in Terminal;
- send only question ID and available letters to the wearable;
- wait for a matching response.

Contract:

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

Internally, each option must also map to a skill:

- research;
- coding;
- none.

Options must come from recent context, unfinished work, and possible next
steps. Do not generate generic unrelated options.

If confidence is insufficient, offer clarification or D only.

D, expiration, owner return, invalid authorization, or no response must
perform no execution.

## 10. Physical workspace duplication

Never modify the original workspace.

Before coding execution:

- require an explicit source workspace path;
- resolve and validate the path;
- create a physical duplicate under an AI Desk session directory;
- do not default to copying the current repository;
- reject filesystem roots, home directories, recursive targets, and unsafe
  paths;
- do not use git worktree;
- exclude:
  - .git;
  - .env;
  - secrets;
  - virtual environments;
  - caches;
  - build artifacts;
  - databases;
  - prior AI Desk execution directories;
- record hashes or metadata for original files;
- create an execution manifest;
- restrict all writes to the duplicate;
- verify original hashes after execution.

The resume field original_files_modified must always be [].

## 11. Demo workspace

The first coding demo must use a safe repository-owned fixture:

`demo_workspace/`

Run it explicitly:

```bash
python run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace
```

Do not provide behavior that silently copies the current directory.

Only after isolation tests and human review may arbitrary user-selected
workspaces be enabled through an explicit path.

## 12. Agent routing

Use separate interfaces:

- ResearchAgentExecutor;
- CodingAgentExecutor.

Research selections use the existing A2A Research Handoff Agent.

Coding selections use:

- FakeCodingExecutor in tests and deterministic simulation;
- CodexCLIExecutor in the real coding demo.

Do not convert the Research Agent into a general coding agent.

## 13. Codex CLI safety

Codex CLI must already be installed.

AI Desk must never install Codex CLI automatically.

If it is unavailable, stop with a clear preflight error.

The Codex adapter must:

- use an injectable subprocess runner;
- never use shell=True;
- never construct an untrusted shell string;
- set cwd to the duplicate;
- use the strictest supported workspace-write sandbox;
- prevent access outside the duplicate where the CLI supports it;
- fail closed if required isolation flags are unavailable;
- enforce a 300-second runtime limit;
- enforce a 20-safe-step limit;
- record commands without secrets;
- never push, publish, deploy, upload, or send messages;
- never silently fall back to an unsafe invocation.

## 14. Codex CLI data authorization

A/B/C selects the desired task, but does not by itself authorize transmitting
workspace context to a model service.

Before starting Codex CLI, display a separate permission question:

> This task will use Codex CLI and may send relevant files from the duplicate
> workspace to the configured model service. Allow?
>
> YES / NO

Codex CLI may start only after a matching YES response.

NO, timeout, stale question ID, owner return, or wearable disconnection must
cancel the Codex invocation.

This authorization is separate from package installation permission.

## 15. Installation permission

A task selection does not authorize dependency installation.

If installation is required:

- enter PERMISSION_PENDING;
- show the exact package and proposed command;
- request wearable YES/NO;
- do not continue until a matching YES;
- NO or timeout cancels installation;
- installation is allowed only in a local virtual environment inside the
  duplicate;
- record approved installations in the manifest.

## 16. External actions

Without separate explicit authorization, never:

- send messages;
- push Git commits;
- publish content;
- upload data;
- deploy;
- modify external services;
- install system software;
- delete user-created files.

For the first implementation, these actions should be denied rather than
implemented.

## 17. Owner return

Confirm owner return when:

- radar == PERSON_PRESENT;
- wearable == OWNER_NEAR;
- resumed mouse/keyboard input may be supporting evidence.

On confirmed return:

- stop starting new work;
- finish only the current atomic safe step;
- persist the result and manifest;
- stop execution safely;
- return control to the user;
- show a resume report;
- never auto-merge duplicate changes into the original.

Resume contract:

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

original_files_modified must always be [] and must be verified using the
original-workspace hashes.

## 18. Testing policy

Preserve all existing MVP tests.

Every new public component requires:

- at least one normal-path test;
- at least one relevant rejection/error-path test.

Safety components require explicit unauthorized-path tests.

Expected test modules include:

- test_activity_classifier.py;
- test_work_context.py;
- test_presence_fusion.py;
- test_wearable_contract.py;
- test_handoff_questions.py;
- test_workspace_duplicator.py;
- test_execution_policy.py;
- test_codex_executor.py;
- test_ocr_policy.py;
- test_return_coordinator.py;
- test_owner_handoff_e2e.py.

Normal tests must never call:

- real OpenAI;
- real Codex CLI;
- Research Agent network service;
- OCR;
- BLE;
- serial hardware;
- package installers;
- external network;
- Git push.

Use fake adapters, injected clocks, temporary directories, mock subprocess
runners, and deterministic executors.

## 19. Documentation policy

README must document:

- architecture;
- state machine;
- all default parameters and overrides;
- radar interface;
- future BLE interface;
- wearable packet contract;
- missing hardware details;
- OCR mock and future adapter;
- physical copy rules;
- excluded files;
- Codex CLI preflight;
- Codex data-upload authorization;
- installation authorization;
- Research/Coding skill routing;
- simulator commands;
- macOS limitations;
- which paths are mocked versus physically verified.

Important safety boundaries must have concise code comments explaining why the
check exists.

Do not add comments that merely restate obvious code.

## 20. Completion rules

Before any final commit:

- all legacy tests pass;
- all new tests pass;
- git diff --check passes;
- no pycache, pyc, secret, database, screenshot, generated report, or
  duplicate workspace is tracked;
- original-workspace safety tests pass;
- the simulated end-to-end flow passes;
- unresolved real hardware verification is clearly reported.

Do not commit or push unless explicitly requested.

---

## Phase 1 Review Addendum (approved 2026-07-25)

This addendum records an approved clarification from Phase 1 review. It does
not delete, weaken, or reinterpret any requirement in sections 1–20 above; it
distinguishes which already-stated values are operator-configurable and which
already-stated behaviors are mandatory safety invariants that must not be
weakened by configuration. Where this addendum and an earlier section could
be read as being in tension, this addendum controls only on the narrow
question of "is this value environment-overridable" — every underlying
behavioral requirement above still applies in full.

**Configurable values** (may be overridden via environment variable, subject
to validation; invalid values must fail clearly rather than silently produce
an unsafe configuration):

- timing durations (e.g. owner leave confirmation seconds, question
  expiration seconds, input idle threshold seconds);
- numeric thresholds;
- database and session paths;
- adapter modes (e.g. simulator vs. real, where a real adapter exists);
- runtime and safe-step caps;
- Codex binary path.

**Mandatory safety invariants** (documented and centralized in code, but
**not** environment-overridable — an override capability here would let a
misconfigured `.env` defeat the exact property the check exists to
guarantee):

- wearable UNKNOWN never triggers (§3, §6);
- contradictory signals always wait (§3, §7);
- external actions remain denied in V1 (§16);
- OCR is callable only after Tier 1 (activity classification) and Tier 2
  (context analysis) both remain AMBIGUOUS (§4);
- an unanswered or timed-out handoff question always resolves to D / "Do
  nothing," and never to AUTHORIZED (§3, §9);
- a missing or unusable Codex CLI always stops safely — this is fixed
  behavior, not a policy value that could later be flipped to an unsafe
  fallback (§3, §13);
- missing Codex isolation support always fails closed (§13);
- the original workspace is never writable (§10);
- Git push/publish/message/deploy remain prohibited (§16).

No environment variable may bypass, relax, or select an alternate policy for
any invariant in this list. Concretely, this retracts six previously
proposed environment variables that would have made invariants
configurable — `AI_DESK_V2_WEARABLE_UNKNOWN_TRIGGERS`,
`AI_DESK_V2_CONTRADICTION_POLICY`, `AI_DESK_V2_EXTERNAL_ACTIONS_ENABLED`,
`AI_DESK_V2_OCR_POLICY`, `AI_DESK_V2_UNANSWERED_DEFAULT`, and
`AI_DESK_V2_MISSING_CODEX_POLICY` — none of which may be added to
`.env.example` or any future configuration surface. The first four were
retracted in the original Phase 1 Review Addendum; the last two are added by
this correction (Phase 1 repair gate, 2026-07-25) after review found them
proposed as configurable in the Phase 0 Report's configuration table, which
has since been corrected to match.

All eight invariants above are backed by real, importable constants in
`application/owner_handoff/config.py`
(`WEARABLE_UNKNOWN_NEVER_TRIGGERS`, `CONTRADICTORY_SIGNALS_POLICY`,
`EXTERNAL_ACTIONS_ENABLED`, `OCR_INVOCATION_ORDER`,
`UNANSWERED_QUESTION_DEFAULT`, `MISSING_CODEX_CLI_POLICY`) — no
documentation in this project should describe an invariant as "centralized
as a constant" unless that constant actually exists in code; two of the
above (`UNANSWERED_QUESTION_DEFAULT`, `MISSING_CODEX_CLI_POLICY`) were added
specifically to make this true after the repair identified them missing.
