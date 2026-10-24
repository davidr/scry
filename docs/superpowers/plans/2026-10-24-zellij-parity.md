# Zellij Backend Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--backend zellij` behave like the tmux backend: attaching moves only the attaching terminal, creating windows moves nobody and the windows survive, rename works, `#` indicator works, window IDs are stable.

**Architecture:** scry runs zellij clients on a pseudo-terminal it owns (`pty_client.py`). For attach, it makes the new client zellij's routing target by sending it configurable no-op "wake" keys, then directs it with `go-to-tab-by-id`, hiding output until the switch is confirmed. For window creation with zero clients attached, it runs a short-lived headless client so new tabs aren't hit by zellij bug #5594. Listing/rename move to tab-ID-based `list-tabs -j` / `rename-tab-by-id`.

**Tech Stack:** Python 3.12 stdlib (`pty`, `os`, `termios`, `tty`, `select`, `signal`, `threading`, `json`), pytest, zellij 0.45.1.

**Spec:** `docs/superpowers/specs/2026-10-24-zellij-parity-design.md` — read it first; its "Verified zellij 0.45.1 facts" are the ground truth this plan relies on.

## Global Constraints

- Target zellij **0.45.1**. No zellij plugins.
- Never touch the user's real `main` session. Live tests use `scry-test-<pid>-<n>` sessions and always `kill-session` + `delete-session` them in teardown.
- Live tests run only when `SCRY_LIVE_ZELLIJ=1` and `zellij` is on PATH; they set `ZELLIJ_CONFIG_DIR` to an empty temp dir so stock keybindings apply (default wake keys `"\x07\x07"` are correct there).
- Every zellij client scry spawns uses `attach <S> options --on-force-close detach`.
- Config key `zellij_wake_keys`, default `"\x07\x07"`; each character is one keypress, written 50 ms apart.
- Ruff clean (`uv run ruff format . && uv run ruff check .`), line length 120. Match existing docstring style (Google-style, `Args:`/`Returns:`/`Raises:`).

## Review Focus

1. **scry crashes or is killed mid-attach** → the user's terminal must not be left in raw mode. Test in Task 3 (`test_proxy_restores_termios_on_error`).
2. **The new client never shows up** (session died, attach failed) → `attach_window` must return, not hang; gate opens on timeout. Test in Task 5 (`test_attach_returns_when_client_never_appears`).
3. **Terminal resized while attached** → zellij must see the new size. Test in Task 3 (`test_proxy_propagates_sigwinch`).
4. **Duplicate tab names created outside scry** → both listed with distinct IDs; each selectable. Test in Task 1 (`test_list_windows_duplicate_names_distinct_ids`).
5. **`l` with many windows and zero clients** → one headless client for the whole batch, not one per window. Test in Task 4 (`test_load_uses_single_headless_client`).

---

### Task 1: Tab-ID listing, `#` indicator, missing-session handling

**Files:**
- Modify: `src/scry/backends/zellij.py` (`_NO_SESSION_MARKERS`, `list_windows`; add `_list_tabs`, `_list_clients`, `_client_tabs`)
- Modify: `tests/test_backend_zellij.py`, `tests/test_backend_contract.py` (zellij `windows_stdout` becomes `list-tabs -j` JSON)

**Interfaces:**
- Produces (on `ZellijBackend`):
  - `_list_tabs(self, session_group: str) -> List[Dict]` — parsed `action list-tabs -j`; raises `RuntimeError` on failure.
  - `_list_clients(self, session_group: str) -> List[str]` — client IDs (strings) from `action list-clients` (skip header line; first column).
  - `_client_tabs(self, session_group: str) -> Dict[str, int]` — client ID (str) → `tab_id`, from each tab's `other_focused_clients`.
  - `list_windows` returns `{"window_id": str(tab_id), "window_name": name, "window_active_clients": str(len(other_focused_clients))}`, sorted by name.

- [ ] **Step 1: Write failing tests** in `tests/test_backend_zellij.py`, replacing `test_list_windows_names_are_id_and_name`. Use a JSON fixture string shaped like real output (keys `tab_id`, `position`, `name`, `active`, `other_focused_clients`; extra keys present):

```python
def test_list_windows_uses_tab_ids_and_client_counts(mock_run, backend):
    mock_run.return_value = _completed(stdout=LIST_TABS_JSON)  # zeta id 2 viewed by [1, 3]; "Tab #1" id 0; alpha id 1
    assert backend.list_windows("main") == [
        {"window_id": "0", "window_name": "Tab #1", "window_active_clients": "0"},
        {"window_id": "1", "window_name": "alpha", "window_active_clients": "0"},
        {"window_id": "2", "window_name": "zeta", "window_active_clients": "2"},
    ]
    assert mock_run.call_args[0][0] == ["/home/user/bin/zellij", "--session", "main", "action", "list-tabs", "-j"]

def test_list_windows_duplicate_names_distinct_ids(...)   # two tabs named "dup" (ids 4, 7) -> both returned, ids "4" and "7"
def test_list_windows_session_not_found_returns_empty(...)  # stderr b"Session 'main' not found. The following sessions are active:\nwork\n" -> []
def test_list_clients_parses_table(...)  # b"CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n1         terminal_5     N/A\n2         terminal_3     N/A\n" -> ["1", "2"]
def test_client_tabs_maps_clients(...)   # LIST_TABS_JSON -> {"1": 2, "3": 2}
```

Update `CASES["zellij"]["windows_stdout"]` in the contract test to a two-tab `list-tabs -j` JSON (beta id 1, alpha id 0).

- [ ] **Step 2: Run** `uv run pytest tests/test_backend_zellij.py tests/test_backend_contract.py -v` — expect the new tests to FAIL.
- [ ] **Step 3: Implement.** Add `"not found. The following sessions are active"` to `_NO_SESSION_MARKERS`. Parse JSON with `json.loads("\n".join(cmd.stdout))`.
- [ ] **Step 4: Run** the same command — expect PASS; then `uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat(zellij): list windows by stable tab ID with client counts`.

---

### Task 2: Rename by tab ID

**Files:**
- Modify: `src/scry/backends/zellij.py` (`rename_window`)
- Modify: `tests/test_backend_zellij.py` (replace `test_rename_window_unsupported`)

**Interfaces:**
- Consumes: `_list_tabs` (Task 1).
- Produces: `rename_window(window_id, new_name, session_group)` runs `action rename-tab-by-id <window_id> <new_name>`, then re-lists; raises `RuntimeError` if no tab has `tab_id == int(window_id)` or its name != `new_name`.

- [ ] **Step 1: Failing tests:** `test_rename_window_by_id` (argv `[..., "action", "rename-tab-by-id", "4", "newname"]`; second call returns JSON where id 4 is `newname` → no raise); `test_rename_window_unknown_id_raises` (post-check JSON lacks id 4 → `RuntimeError` matching `"no window"`); `test_rename_window_not_applied_raises` (id 4 still old name → `RuntimeError` matching `"did not take effect"`).
- [ ] **Step 2: Run** `uv run pytest tests/test_backend_zellij.py -k rename -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(zellij): support window rename via rename-tab-by-id`.

---

### Task 3: Pseudo-terminal client and proxy

**Files:**
- Create: `src/scry/backends/pty_client.py`
- Create: `tests/test_pty_client.py`

**Interfaces:**
- Produces:
  - `class PtyClient` — `__init__(self, argv: List[str], rows: int, cols: int)` spawns argv as a session leader on a new pty (child's stdin/stdout/stderr = pty slave); attributes `master_fd: int`, `pid: int`. Methods: `write(self, data: bytes) -> None`, `resize(self, rows: int, cols: int) -> None` (TIOCSWINSZ on master), `is_alive(self) -> bool`, `wait(self) -> int`, `terminate(self, timeout: float = 2.0) -> None` (SIGHUP, wait, then SIGKILL; close master; idempotent), `start_draining(self) -> None` (daemon thread reads and discards output — for headless clients).
  - `proxy(client: PtyClient, gate: threading.Event, hold_timeout: float = 2.0, stdin_fd: int = 0, stdout_fd: int = 1) -> None` — runs until the child exits. Raw mode on `stdin_fd` only if `os.isatty(stdin_fd)`; termios restored in `finally`. While the gate is unset and `hold_timeout` hasn't elapsed, pty output and stdin input are buffered; on release, flush output in one `os.write`, then forward buffered input. On stdin EOF, `client.terminate()`. If `stdin_fd` is a tty, install a SIGWINCH handler that resizes the pty to the terminal's size (restore the previous handler on exit). Use `select` on `[master_fd, stdin_fd]` with a short timeout so the hold deadline is checked.

- [ ] **Step 1: Failing tests** (real ptys, no zellij):
  - `test_ptyclient_reports_size` — `PtyClient(["sh", "-c", "stty size"], 30, 100)`; read master until EOF; output contains `"30 100"`.
  - `test_ptyclient_resize` — child `sh -c 'sleep 0.3; stty size'`; `resize(40, 120)` immediately; output contains `"40 120"`.
  - `test_ptyclient_terminate_idempotent` — `["sleep", "30"]`; `terminate()` twice; `is_alive()` is False.
  - `test_proxy_holds_until_gate` — stdin/stdout are `os.pipe()` pairs; child `sh -c 'echo hello; sleep 1'`; gate unset, `hold_timeout=5`; after 0.3 s, stdout pipe has nothing (non-blocking read); `gate.set()`; within 0.5 s it reads `hello`.
  - `test_proxy_releases_on_timeout` — same, never set gate, `hold_timeout=0.3`; `hello` arrives.
  - `test_proxy_forwards_held_input_after_release` — child `cat`; write `b"abc\n"` to stdin pipe before release; after `gate.set()` output contains `abc`.
  - `test_proxy_stdin_eof_terminates_child` — child `sleep 30`; close stdin write end; `proxy` returns within 3 s.
  - `test_proxy_restores_termios_on_error` — `stdin_fd` is the slave side of a fresh `os.openpty()` (a tty); capture `termios.tcgetattr` before; make `os.write` to stdout raise `OSError` via patch; `proxy` raises; attrs equal the captured ones.
  - `test_proxy_propagates_sigwinch` — `stdin_fd` is a pty slave set to 50×160 via TIOCSWINSZ; child `sh -c 'sleep 0.5; stty size'`; after start, `os.kill(os.getpid(), signal.SIGWINCH)` from a timer thread; output contains `"50 160"`. (Run `proxy` on the main thread — signal handlers require it.)
- [ ] **Step 2: Run** `uv run pytest tests/test_pty_client.py -v` — FAIL (module missing).
- [ ] **Step 3: Implement** `pty_client.py` (spawn with `os.openpty()` + `subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)`, close slave in parent; set size before spawn).
- [ ] **Step 4: Run** — PASS; full suite `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat: add pty client and gated terminal proxy`.

---

### Task 4: Window creation that moves nobody and survives attach

**Files:**
- Modify: `src/scry/backends/base.py` (add `creating_windows`)
- Modify: `src/scry/backends/zellij.py` (`create_detached_window`, `_headless_client`, `creating_windows`, `_wait_for_new_client`)
- Modify: `src/scry/scry.py` (`load_windows_from_yaml` wraps its creation loop in `backend.creating_windows(session_group)`)
- Modify: `tests/test_backend_zellij.py`, `tests/test_file_operations.py`
- Create: `tests/test_zellij_live.py` (with the live-test fixture: skip unless `SCRY_LIVE_ZELLIJ=1` and zellij on PATH; `monkeypatch.setenv("ZELLIJ_CONFIG_DIR", <empty tmp dir>)`; yields a fresh `scry-test-<pid>-<n>` name; teardown kills hosted `PtyClient`s, `kill-session`, `delete-session`)
- Modify: `pyproject.toml` (register `live` marker under `[tool.pytest.ini_options]`)

**Interfaces:**
- Consumes: `PtyClient` (Task 3), `_list_clients` (Task 1).
- Produces:
  - `MuxBackend.creating_windows(self, session_group: str) -> ContextManager[None]` — concrete default returns `contextlib.nullcontext()`.
  - `ZellijBackend._wait_for_new_client(self, session_group: str, known: Set[str], timeout: float = 3.0) -> Optional[str]` — polls `_list_clients` every 50 ms; returns the first ID not in `known`, or `None`.
  - `ZellijBackend._headless_client(self, session_group)` — `@contextmanager`: if `_list_clients` is non-empty, yield without spawning; else spawn `PtyClient([bin, "attach", S, "options", "--on-force-close", "detach"], 24, 80)`, `start_draining()`, wait for it to appear (raise `RuntimeError("zellij helper client did not attach")` on timeout, after terminating it), yield, then `terminate()` in `finally`. Re-entrant: nested use while one is active doesn't spawn a second (track with an instance counter).
  - `creating_windows` returns `self._headless_client(session_group)`.
  - `create_detached_window`: same guards; then within `_headless_client`, `action new-tab --no-focus --name <name>`.

- [ ] **Step 1: Failing unit tests** (patch `PtyClient` and `_list_clients`):
  - `test_create_window_no_focus_argv` — clients present → no `PtyClient` constructed; argv ends `["new-tab", "--no-focus", "--name", "newwin"]`.
  - `test_create_window_zero_clients_spawns_headless` — `_list_clients` returns `[]`, then `["9"]`; `PtyClient` called with argv `[bin, "attach", "main", "options", "--on-force-close", "detach"]`; `terminate` called after `new-tab`.
  - `test_headless_client_timeout_raises_and_cleans_up` — `_list_clients` always `[]`; `RuntimeError` matching `"did not attach"`; `terminate` called.
  - `test_load_uses_single_headless_client` (in `tests/test_file_operations.py`, using a real `ZellijBackend` with patched subprocess/`PtyClient`, YAML listing 3 new windows, zero clients) — `PtyClient` constructed exactly once; 3 `new-tab` calls.
- [ ] **Step 2: Failing live tests** in `tests/test_zellij_live.py` (marked `live`):
  - `test_live_tab_created_with_zero_clients_survives_attach` — `ensure_session_group`; `create_detached_window("x", S)`; host a `PtyClient` attach + `start_draining`; wait 1.5 s; `"x"` in `[w["window_name"] for w in backend.list_windows(S)]`.
  - `test_live_create_moves_no_client` — host client A, write `b"\x07\x07"` to it (makes it routing target); record `_client_tabs`; `create_detached_window("y", S)`; `_client_tabs` unchanged.
  - `test_live_rename` — rename `"x"`'s ID to `"x2"` with zero clients; listed name is `"x2"`.
- [ ] **Step 3: Run** `uv run pytest tests/test_backend_zellij.py tests/test_file_operations.py -v` and `SCRY_LIVE_ZELLIJ=1 uv run pytest tests/test_zellij_live.py -v` — FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** both commands — PASS; `uv run pytest -q` (live tests skipped) — PASS.
- [ ] **Step 6: Commit** `feat(zellij): create tabs without moving clients or hitting zellij #5594`.

---

### Task 5: Attach that moves only the attaching terminal

**Files:**
- Modify: `src/scry/backends/zellij.py` (remove `_refocus_tab`, `_ATTACH_REFOCUS_DELAYS`, `threading`/`time` refocus usage; rewrite `attach_window`; add `_attach`, `_direct_client`; constructor takes `wake_keys`)
- Modify: `src/scry/backends/__init__.py` (`ZellijBackend(wake_keys=cfg.get("zellij_wake_keys", DEFAULT_WAKE_KEYS))`)
- Modify: `src/scry/scry.py` (`default_config["zellij_wake_keys"] = "\x07\x07"`)
- Modify: `tests/test_backend_zellij.py` (drop refocus/attach tests), `tests/test_backends_factory.py`, `tests/test_zellij_live.py`

**Interfaces:**
- Consumes: `PtyClient`, `proxy` (Task 3); `_list_clients`, `_client_tabs`, `_list_tabs` (Task 1); `_wait_for_new_client` (Task 4).
- Produces:
  - `DEFAULT_WAKE_KEYS = "\x07\x07"` in `scry.backends.zellij`; `ZellijBackend.__init__(self, wake_keys: str = DEFAULT_WAKE_KEYS)`.
  - `_attach(self, window_id: str, session_group: str, stdin_fd: int = 0, stdout_fd: int = 1, hold_timeout: float = 2.0) -> None` — the whole attach; `attach_window(window_id, session_group)` calls it with defaults. Terminal size from `shutil.get_terminal_size()`.
  - `_direct_client(self, session_group: str, tab_id: int, client: PtyClient, known: Set[str], before: Dict[str, int], gate: threading.Event) -> None` — algorithm below; always sets `gate` (in `finally`).

`_direct_client` algorithm (spec "Attach" step 3):

```
new_id = _wait_for_new_client(S, known)            # None -> return
for attempt in range(3):
    for ch in wake_keys: client.write(ch.encode()); sleep(0.05)
    run action go-to-tab-by-id <tab_id>
    poll _client_tabs(S) every 50 ms for up to 1 s:
        if tabs.get(new_id) == tab_id: return
        for cid, tab in tabs.items():
            if cid != new_id and cid in before and tab != before[cid]:
                run action go-to-tab-by-id <before[cid]>   # displaced client is now the routing target
                break  -> next attempt
```

- [ ] **Step 1: Failing unit tests** (patch `PtyClient`, `proxy`, `_list_clients`, `_client_tabs`, `subprocess.run`):
  - `test_attach_spawns_client_with_detach_on_force_close` — `PtyClient` argv `[bin, "attach", "main", "options", "--on-force-close", "detach"]`; `proxy` called with that client.
  - `test_direct_client_wakes_then_focuses` — new client `"3"` appears; client `write` calls are `b"\x07"`, `b"\x07"` (default keys) before the `go-to-tab-by-id 5` argv; `_client_tabs` then reports `{"3": 5}`; gate set; only one go-to issued.
  - `test_direct_client_restores_displaced_client` — `before={"1": 2}`; first poll reports `{"1": 5, "3": 2}`; asserts `go-to-tab-by-id 2` issued next, then a second wake + `go-to-tab-by-id 5`; second poll `{"1": 2, "3": 5}`; gate set.
  - `test_direct_client_custom_wake_keys` — `ZellijBackend(wake_keys="\x01\x1b")` writes `b"\x01"`, `b"\x1b"`.
  - `test_attach_returns_when_client_never_appears` — `_wait_for_new_client` returns `None`; gate set; no go-to issued.
  - `test_get_backend_passes_wake_keys` (factory test) — `get_backend({"backend": "zellij", "zellij_wake_keys": "\x01\x1b"})._wake_keys == "\x01\x1b"`.
- [ ] **Step 2: Failing live test** `test_live_attach_moves_only_new_client`: tabs a, b, c; host clients A and B (draining); move A to a and B to b by writing wake keys to each then `go-to-tab-by-id`; make A the routing target (wake keys to A); run `backend._attach(<id of c>, S, stdin_fd=r, stdout_fd=w)` in a thread with pipes; within 3 s, `_client_tabs` shows the new client on c, A still on a, B still on b; close the stdin pipe's write end → `_attach` returns within 3 s.
- [ ] **Step 3: Run** `uv run pytest tests/test_backend_zellij.py tests/test_backends_factory.py -v` and the live test — FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** both — PASS; `uv run pytest -q` — PASS.
- [ ] **Step 6: Manual check (David, real terminals, throwaway session `scry-test-manual`):** two terminals attached to tabs a and b, type in the first; in a third run `scry -b zellij -s scry-test-manual` with `zellij_wake_keys` set, pick tab c → third terminal shows c with no visible flash of another tab; first two unchanged. Kill the session afterwards.
- [ ] **Step 7: Commit** `feat(zellij): direct only the attaching client via wake keys and a gated pty proxy`.

---

### Task 6: Docs and leftovers

**Files:**
- Modify: `src/scry/scry.py` (`"Invalid tmux window name"` → `"Invalid window name"`; update the matching assertion in `tests/test_commands.py` if any)
- Modify: `README.md` (zellij section: version floor 0.45.1; remove the "no `#`", "rename not supported", "first client" limitations; document `zellij_wake_keys` with both examples and the warning that a wrong value types into the pane; mention zellij #5594 and the helper client; add `pty_client.py` to the layout list; mention `SCRY_LIVE_ZELLIJ=1 uv run pytest -m live`)
- Modify: `CLAUDE.md` (Key Design Decisions zellij bullet; backends list adds `pty_client.py`; test count from `uv run pytest -q`)
- Modify: `docs/superpowers/specs/2026-07-06-mux-backend-design.md` (one-line note at the top of "Zellij backend semantics" and "Spike results": superseded by `2026-10-24-zellij-parity-design.md`; routing is last-active client, not first client)

- [ ] **Step 1: Make the edits.**
- [ ] **Step 2: Run** `uv run ruff format . && uv run ruff check . && uv run pytest -q` — clean, all pass.
- [ ] **Step 3: Commit** `docs: document zellij parity, wake keys, and 0.45.1 requirement`.
