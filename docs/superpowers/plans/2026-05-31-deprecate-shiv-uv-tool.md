# Replace shiv with `uv tool install` Distribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile shiv single-executable instructions with a uv-native distribution path so that a fresh checkout produces a runnable `scry` command via one `make install`.

**Architecture:** `scry` is distributed as a uv tool. `make install` runs `uv tool install .`, which builds the project with the existing `uv_build` backend and the `[project.scripts] scry = "scry.__main__:run_scry"` entry point, installing a `scry` executable into uv's tool-bin dir (`~/.local/bin`). The target machine needs only `uv` (it bootstraps its own Python + locked deps). A one-line fix to `__main__.py` removes a latent double-invocation so the installed executable launches the TUI exactly once.

**Tech Stack:** Python 3.12+, uv (`uv_build` backend), Make, pytest.

**Working location:** Worktree at `~/worktrees/scry/deprecate-shiv-uv-tool` (branch `deprecate-shiv-uv-tool`). Run all commands from there.

---

### Task 1: Fix latent double-invocation in `__main__.py`

`src/scry/__main__.py` both calls `run_scry()` at module level and exposes
`run_scry` as the console-script entry point. uv's generated `scry` executable
imports the module (running the loop once) then calls `run_scry()` again. The
fix: guard the module-level call behind `if __name__ == "__main__":` so the
entry point is the sole caller and importing the module is side-effect free.

**Files:**
- Modify: `src/scry/__main__.py`
- Test: `tests/test_main.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main.py`:

```python
"""Tests for the scry.__main__ entry point."""

import sys
from unittest.mock import patch


def test_importing_main_does_not_launch_loop():
    """Importing the module must not run the interactive loop (latent
    double-invocation: the console-script imports the module AND calls
    run_scry, so an import-time call would run the loop twice)."""
    sys.modules.pop("scry.__main__", None)
    with patch("scry.scry.do_table_loop") as mock_loop:
        import scry.__main__  # noqa: F401

        assert mock_loop.call_count == 0


def test_run_scry_invokes_loop_once():
    """run_scry() is the single entry point and runs the loop exactly once."""
    import scry.__main__ as main_mod

    with patch("scry.__main__.do_table_loop") as mock_loop:
        main_mod.run_scry()

        mock_loop.assert_called_once()
```

- [ ] **Step 2: Run tests to verify the import test fails**

Run: `uv run pytest tests/test_main.py -v`
Expected: `test_importing_main_does_not_launch_loop` FAILS — the current
module-level `run_scry()` call invokes `do_table_loop` during import, so
`call_count` is 1, not 0. (`test_run_scry_invokes_loop_once` passes already.)

- [ ] **Step 3: Apply the fix**

Replace the entire contents of `src/scry/__main__.py` with:

```python
#!/usr/bin/env python3

from scry.scry import do_table_loop


def run_scry():
    do_table_loop()


if __name__ == "__main__":
    run_scry()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: all tests PASS (the prior 89 plus the 2 new ones).

- [ ] **Step 6: Commit**

```bash
git add src/scry/__main__.py tests/test_main.py
git commit -m "$(cat <<'EOF'
Make scry.__main__ import side-effect free

Guard the module-level run_scry() call behind __name__ == "__main__" so
the generated console script (which imports the module and then calls
run_scry) launches the TUI once instead of twice.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add the Makefile

Thin wrappers over uv. `install` is the default goal so bare `make` works.

**Files:**
- Create: `Makefile` (repo root)

- [ ] **Step 1: Create `Makefile`**

```makefile
.DEFAULT_GOAL := install

.PHONY: install reinstall uninstall test

install:
	uv tool install .

reinstall:
	uv tool install . --reinstall

uninstall:
	uv tool uninstall scry

test:
	uv run pytest -v
```

> Note: the recipe lines must be indented with a real TAB, not spaces, or
> `make` will error with "missing separator".

- [ ] **Step 2: Verify the Makefile parses and targets resolve (dry run)**

Run: `make -n install reinstall uninstall test`
Expected output (no execution, just the commands that would run):

```
uv tool install .
uv tool install . --reinstall
uv tool uninstall scry
uv run pytest -v
```

(We use `-n` here to avoid actually installing scry into the user's
environment during plan execution. Real installation is the final manual
verification step.)

- [ ] **Step 3: Confirm `install` is the default goal**

Run: `make -n`
Expected: `uv tool install .`

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "$(cat <<'EOF'
Add Makefile for uv tool install distribution

make install -> uv tool install . ; plus reinstall, uninstall, test.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Update README — remove shiv, document `make install`

**Files:**
- Modify: `README.md` (remove lines 35–51, the "Alternative: Using `shiv`"
  section; add an "Install as a command" subsection)

- [ ] **Step 1: Remove the shiv section**

Delete this entire block from `README.md` (currently lines 35–52, including the
trailing blank line before `## Configuration`):

```markdown
### Alternative: Using `shiv` (for a single executable)

If you prefer a single executable file and have `shiv` installed:

\```bash
# Ensure dependencies are installed for shiv to package them
# Example using a temporary uv environment:
# uv venv .shiv-build-env
# source .shiv-build-env/bin/activate
# uv pip install rich PyYAML # Install direct dependencies

shiv -o /tmp/scry -c scry . --python "/usr/bin/env python3"

# Deactivate if you used a temporary env for shiv
# deactivate
\```
*Note: The `shiv` process might need adjustments based on how `shiv` discovers dependencies when a `pyproject.toml` is present. You might need to explicitly install dependencies into the environment `shiv` is using or point it to a requirements file generated by `uv pip freeze > requirements.txt`.*
```

- [ ] **Step 2: Add the "Install as a command" subsection**

In its place (after the "Recommended Installation" subsection, before
`## Configuration`), insert:

```markdown
### Install as a command (on your PATH)

To get a `scry` executable on your PATH from a fresh checkout, run:

\```bash
make install        # equivalent to: uv tool install .
\```

This builds `scry` and installs it as a [uv tool](https://docs.astral.sh/uv/guides/tools/),
placing the `scry` executable in uv's tool-bin directory (`~/.local/bin` by
default). The only requirement on the machine is `uv` — it bootstraps its own
Python interpreter and the locked dependencies into an isolated environment.

\```bash
make reinstall      # uv tool install . --reinstall (after pulling changes)
make uninstall      # uv tool uninstall scry
\```

If `scry` isn't found after install, ensure uv's tool-bin directory is on your
PATH (`uv tool update-shell` sets this up).
```

- [ ] **Step 3: Verify shiv is gone and the new section is present**

Run: `grep -n -i "shiv" README.md ; grep -n "Install as a command" README.md`
Expected: the first `grep` prints nothing (exit status 1); the second prints
the new heading line.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
README: replace shiv instructions with uv tool install

Drop the fragile shiv single-executable section in favor of
make install / uv tool install . for getting scry on PATH.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Update CLAUDE.md — document the distribution path

**Files:**
- Modify: `CLAUDE.md` ("Build & Development" section)

- [ ] **Step 1: Extend the Build & Development section**

In `CLAUDE.md`, the "Build & Development" section currently contains this code
block:

```bash
uv sync                      # install project + dev dependencies
source .venv/bin/activate
scry                         # run the CLI
uv run pytest -v             # run the test suite
```

Add these lines to that same code block, after the `uv run pytest -v` line:

```bash
make install                 # uv tool install . — install scry as a uv tool on PATH
make uninstall               # uv tool uninstall scry
```

Then add this sentence immediately after the code block:

```markdown
Distribution is via `uv tool install` (wrapped by the `Makefile`); the target machine needs only `uv`, which bootstraps its own Python and the locked dependencies. There is no shiv/zipapp build anymore.
```

- [ ] **Step 2: Verify the changes**

Run: `grep -n "uv tool install\|make install" CLAUDE.md`
Expected: matches in both the code block and the new sentence.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
CLAUDE.md: document uv tool install distribution path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Final manual verification (real install)

This task actually mutates the environment (installs scry), so it is separated
from the automated steps. Run it once at the end.

- [ ] **Step 1: Install from the clean checkout**

Run: `make install`
Expected: uv builds and installs scry; output ends with an entry like
`Installed executable: scry` (or similar) and no errors.

- [ ] **Step 2: Confirm the executable is on PATH**

Run: `which scry`
Expected: a path under uv's tool-bin dir (e.g. `~/.local/bin/scry`).

- [ ] **Step 3: Confirm it launches exactly once**

Run `scry` from a shell inside tmux, quit with `q`, and confirm it returns to
the prompt immediately (it does NOT relaunch a second time — this validates the
Task 1 fix).

- [ ] **Step 4 (optional): Clean up**

Run: `make uninstall` if you don't want scry left installed from the worktree.

---

## Self-Review Notes

- **Spec coverage:** Makefile (Task 2), `uv tool install` model (Tasks 2 & 5),
  `__main__.py` double-invocation fix (Task 1), README shiv removal + install
  docs (Task 3), CLAUDE.md update (Task 4), verification (Tasks 1.5, 5). All
  spec sections covered.
- **No placeholders:** every code/Make/markdown step shows full content.
- **Consistency:** `run_scry` / `do_table_loop` names match the actual source;
  the entry point in `pyproject.toml` (`scry.__main__:run_scry`) is unchanged
  and still valid after the Task 1 fix.
