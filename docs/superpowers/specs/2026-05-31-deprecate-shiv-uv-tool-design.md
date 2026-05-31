# Design: Replace shiv with `uv tool install` distribution

**Date:** 2026-05-31
**Status:** Approved

## Problem

The README documents producing a single `scry` executable with `shiv`. The
shiv instructions are fragile (they note that dependency discovery "might need
adjustments") and predate the project's move to a fully uv-managed workflow.
We want the modern, uv-native way to get a runnable `scry` command from a fresh
checkout with a single command.

Note: there is no shipped `uv-bundler` / `uv bundle` tool. A built-in uv
bundling command is only an open feature request (astral-sh/uv#5802). The
current state of the art for "I have a uv project and want an executable on my
PATH" is `uv tool install`.

## Goal

From a fresh checkout, a single command produces a `scry` executable that any
shell can run to launch scry.

## Decisions

- **Runtime model:** the target machine must have `uv` installed. `uv`
  bootstraps its own Python interpreter and the locked dependencies into an
  isolated tool environment — nothing else is required on the target.
- **Mechanism:** `uv tool install .`. uv builds the project using the existing
  `uv_build` backend and the `[project.scripts] scry = "scry.__main__:run_scry"`
  entry point, then installs a real `scry` executable into uv's tool-bin
  directory (`~/.local/bin` by default). Any shell with that directory on PATH
  runs `scry`.
- **Single command:** a `Makefile` provides memorable entry points wrapping uv.

## Changes

### 1. `Makefile` (new, repo root)

Thin wrappers over uv. `install` is the default goal so bare `make` works.

| Target | Command |
|---|---|
| `install` (default) | `uv tool install .` |
| `reinstall` | `uv tool install . --reinstall` |
| `uninstall` | `uv tool uninstall scry` |
| `test` | `uv run pytest -v` |

All targets `.PHONY`.

### 2. `src/scry/__main__.py` (fix latent double-invocation)

Today the module calls `run_scry()` at module level *and* exposes `run_scry` as
the console-script entry point. When uv generates the `scry` executable it
imports the module (running the loop once) and then calls `run_scry()` again
(running it a second time). Remove the module-level `run_scry()` call so the
entry point is the sole caller.

After the change, `__main__.py` keeps the `if __name__ == "__main__":` guard
behavior — i.e. it still runs when executed directly (`python -m scry`) but does
not run merely on import.

```python
#!/usr/bin/env python3

from scry.scry import do_table_loop


def run_scry():
    do_table_loop()


if __name__ == "__main__":
    run_scry()
```

### 3. `README.md`

- Delete the entire "Alternative: Using `shiv` (for a single executable)"
  section.
- Add an "Install as a command" subsection under Installation documenting:
  - `make install` (or `uv tool install .`) from a fresh checkout
  - that it requires `uv` on the target
  - updating with `make reinstall`
  - removing with `make uninstall`

### 4. `CLAUDE.md`

Add the `Makefile` / `uv tool install` distribution path to the "Build &
Development" section so the documented build instructions stay accurate.

## Out of scope

- True self-contained binaries that bundle a Python interpreter (PyApp,
  PyInstaller, py-app-standalone). Explicitly rejected: the chosen model
  assumes `uv` is present on the target.
- Any change to scry's runtime behavior, config, or tmux logic.

## Verification

- `make install` from a clean checkout produces a working `scry` on PATH;
  running `scry` launches the TUI exactly once (confirms the double-invocation
  fix).
- `make uninstall` removes it.
- `make test` (the existing 89-test suite) still passes.
