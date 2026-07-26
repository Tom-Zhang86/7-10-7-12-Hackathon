# AI Desk V2 demo fixture

`sample_project/` is a tiny, dependency-free Python "project": two files,
no third-party packages, no build step.

- `calculator.py` has exactly one deliberate, obvious, small, reversible
  bug: `add(a, b)` subtracts instead of adding.
- `test_calculator.py` fails on that bug alone (`test_add`); `test_multiply`
  already passes.

Verify the bug yourself, standalone, any time:

```bash
cd demo_workspace/sample_project
python -m unittest -v
```

You should see `test_add` fail and `test_multiply` pass.

This fixture exists only so `run_owner_handoff_demo.py` (repo root) has a
small, real, git-free directory to physically duplicate and (in `--executor
fake` mode) simulate "fixing." AI Desk never modifies anything under
`demo_workspace/` directly -- it always works inside a duplicate created
under a session root, and nothing is ever copied back automatically. See
the root `README.md`'s "AI Desk V2 -- macOS demo" section for the full
walkthrough.
