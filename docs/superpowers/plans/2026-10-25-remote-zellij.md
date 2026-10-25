# Remote Zellij Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every tab of each configured remote zellij session in the local scry index (`b0`, `b1`, …), refresh them on `z`, and attach the user's terminal straight to a remote tab over `ssh -t`, with the remote scry doing the zellij work.

**Architecture:** A new `scry.remote` package holds both ends. The remote end (`server.py`, `identity.py`) is a headless `remote-list` / `remote-attach` dispatch that `__main__.run_scry()` routes to **before** `scry.scry` is imported, so the import-time config parse and backend construction never run there. The local end (`config.py`, `cache.py`, `refresh.py`, `attach.py`) validates `remotes`, runs parallel `ssh -T -o BatchMode=yes` refreshes into per-host JSON cache files, and runs the foreground `ssh -t` attach with a Ctrl-C guard and terminal restore. `scry.py` reads the cache files (never the network) each loop iteration, draws remote sections, and resolves `<key><n>` against that drawn snapshot.

**Tech Stack:** Python 3.12 stdlib (`subprocess`, `threading`, `signal`, `termios`, `fcntl`, `json`, `shlex`, `tempfile`), Rich (`rich.markup.escape`, `rich.text.Text`), PyYAML, pytest, zellij 0.45.1, OpenSSH.

**Spec:** `docs/superpowers/specs/2026-10-25-remote-zellij-design.md` — read it in full first. `docs/superpowers/specs/2026-10-25-zellij-attach-hardening-notes.md` explains the attach lock; its options A–E are **not** in this plan.

## Global Constraints

- Best-effort, not bulletproof: single user, ~10 terminals, 2–3 remote hosts. No safeguards for rare races, adversarial input, or scale beyond what a task states.
- Out of scope: remote rename, remote create, strict attach, any background / age-based / refresh-on-return refresh, the piggyback refresh, `d`/`l` for remote tabs.
- A redraw never touches the network. scry opens ssh only on `z` or a remote attach.
- Remote zellij requires >= 0.45.1, same as the local backend. Session identity code is Linux-only (`/proc`).
- Remote `remote-*` subcommands never enter `do_table_loop`, never call `ensure_session_group`, and validate the remote config before constructing a backend.
- Remote args are passed as `--opt=value`. Never disable ssh host-key checking.
- `remote_timeout` default `10` (seconds per host, whole refresh). Framed response size cap `1_000_000` bytes.
- Names, labels, and remote diagnostic text render as literal text (no Rich markup), control characters replaced.
- Ctrl-C guard: a **no-op Python handler**, never `signal.SIG_IGN`.
- Tests: `uv run pytest -v`. Lint: `uv run ruff format . && uv run ruff check . --fix` (line length 120). Google-style docstrings with `Args:`/`Returns:`/`Raises:`, matching existing modules.
- Live tests and manual checks use throwaway `scry-test-<pid>-<n>` sessions and clean them up. Never touch the real `main` tmux or zellij session group. Wake keys must be a no-op under the target's keybindings (live tests use stock keybindings, so `"\x07\x07"`).
- Commit after each task on branch `remote-zellij` (worktree `/home/davidr/worktrees/scry/remote-zellij`). Never commit to `main`. End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Fixed values (used across tasks)

| Name | Value | Where |
|---|---|---|
| `PROTO_VERSION` | `1` | `scry/remote/protocol.py` |
| `LIST_BEGIN` / `LIST_END` | `"<<<SCRY-REMOTE-LIST-BEGIN>>>"` / `"<<<SCRY-REMOTE-LIST-END>>>"` (each on its own line) | `protocol.py` |
| `MAX_RESPONSE_BYTES` | `1_000_000` | `protocol.py` |
| Exit codes | `EXIT_FAILURE=80`, `EXIT_STALE=81`, `EXIT_TAB_GONE=82`, `EXIT_NO_SESSION=83`, `EXIT_CONFIG=84`, `EXIT_IDENTITY=85`, `EXIT_PROTO=86` (clear of 1, 2, 126, 127, 128+n, 255) | `protocol.py` |
| Session identity string | `f"{pid}:{starttime}"` (starttime = `/proc/<pid>/stat` field 22, clock ticks) | `identity.py` |
| Attach lock | `$XDG_RUNTIME_DIR` (fallback `/tmp`) `/scry-attach-<sanitized session>.lock`, wait `5.0` s, poll `0.05` s | `backends/zellij.py` |
| Terminal reset | `"\x1b[?1049l\x1b[?25h\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1004l\x1b[?2004l\x1b[<u"` | `remote/attach.py` |
| Default cache dir | `$XDG_CACHE_HOME/scry/remotes`, else `~/.cache/scry/remotes` | `remote/config.py` |
| Bare command letters (key warning) | `s q z u d l n r` | `remote/config.py` |

## Review Focus

1. **Remote login shell prints junk, including invalid UTF-8, around the framed JSON** (a `~/.bashrc` banner) → the refresh still succeeds. Test in Task 4 (`test_unframe_ignores_invalid_utf8_noise`).
2. **A host times out mid-output** (killed after `BEGIN` but before `END`) → protocol failure, last known tabs kept, header "unavailable". Test in Task 9 (`test_refresh_timeout_mid_output_keeps_tabs`).
3. **First `z` ever, cache dir doesn't exist; or it's unwritable** → the directory is created; if it can't be, `z` reports an error message and scry keeps running. Test in Task 8 (`test_write_cache_creates_dir`, `test_write_cache_unwritable_raises_oserror`) and Task 9 (`test_refresh_unwritable_cache_reports_message`).
4. **Remote sections make the index taller than the terminal** (3 hosts × 20 tabs on a short window) → redraw doesn't raise. Test in Task 10 (`test_setup_display_overflowing_height_does_not_raise`).
5. **Two scry instances run `z` at once** → no temp files left behind and every read sees a whole file. Test in Task 8 (`test_write_cache_is_atomic_and_leaves_no_temp_files`).

---

## Phase 1 — Local improvements (each shippable alone)

### Task 1: Attach lock in `ZellijBackend._attach`

**Files:**
- Modify: `src/scry/backends/zellij.py` (`_attach`, `_direct_client`; add `_attach_lock_path`, `_acquire_attach_lock`, `_release_attach_lock`, constants)
- Test: `tests/test_backend_zellij.py`

**Interfaces:**
- Produces:
  - `_ATTACH_LOCK_TIMEOUT = 5.0`, `_ATTACH_LOCK_POLL = 0.05`
  - `_attach_lock_path(session_group: str) -> str` — `os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"scry-attach-{safe}.lock")`, where `safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_group)`.
  - `_acquire_attach_lock(session_group: str, timeout: float = _ATTACH_LOCK_TIMEOUT) -> Optional[int]` — opens the file (`O_RDWR | O_CREAT`, 0o600), polls `fcntl.flock(fd, LOCK_EX | LOCK_NB)`; returns the fd, or closes it, logs WARNING `"attach lock for %s busy after %.0fs; attaching without it"` and returns `None`.
  - `_release_attach_lock(fd: Optional[int]) -> None` — idempotent-safe: no-op on `None`; unlock + close.
  - `_direct_client(...)` gains a final keyword arg `lock_fd: Optional[int] = None`; its `finally` sets the gate **then** releases the lock.

- [ ] **Step 1: Write failing tests** in `tests/test_backend_zellij.py` (use `monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))`):
  - `test_attach_lock_path_uses_runtime_dir_and_sanitizes` — session `"a/b"` → `tmp_path / "scry-attach-a_b.lock"`; with `XDG_RUNTIME_DIR` unset → starts with `/tmp/`.
  - `test_attach_takes_lock_before_spawning_client` — patch `_acquire_attach_lock` with a mock that records call order alongside `PtyClient` (use a shared `MagicMock` manager); assert acquire precedes `PtyClient(...)`, and the fd is passed to `_direct_client` as `lock_fd`.
  - `test_direct_client_releases_lock_when_done` — real `_acquire_attach_lock` in tmp dir; run `_direct_client` on the success path (as `test_direct_client_wakes_then_focuses` does) with `lock_fd=fd`; afterwards a second `_acquire_attach_lock(..., timeout=0.1)` returns an fd (not `None`). Repeat on the error path (`_action` raises `RuntimeError`).
  - `test_second_attach_waits_for_lock` — acquire in the test; start a thread calling `_acquire_attach_lock(s, timeout=2.0)`; release after 0.2 s; thread's result is an int and it took >= 0.2 s.
  - `test_busy_lock_times_out_with_warning` — hold the lock; `_acquire_attach_lock(s, timeout=0.2)` returns `None`; `caplog` has a WARNING containing `"busy"`.
  - `test_attach_releases_lock_if_spawn_fails` — `PtyClient` raises `OSError`; afterwards the lock is acquirable.
- [ ] **Step 2: Run** `uv run pytest tests/test_backend_zellij.py -k lock -v` — FAIL (`AttributeError: _attach_lock_path`).
- [ ] **Step 3: Implement.** In `_attach`, acquire right after the tab-exists check and before `PtyClient`; wrap the rest in `try/finally: _release_attach_lock(fd)` only for the case where the director never started (track with a flag; the director owns release once started). The lock must **not** be held for the proxy's lifetime after the director finishes. Remote attach (Task 6) gets this for free because it calls `attach_window`.
- [ ] **Step 4: Run** `uv run pytest tests/test_backend_zellij.py -v` then `uv run pytest -q` — all PASS.
- [ ] **Step 5: Docs.** CLAUDE.md "Key Design Decisions" zellij bullet: add one sentence — attach holds an `flock` on `$XDG_RUNTIME_DIR/scry-attach-<session>.lock` during steering only (5 s wait, then proceeds with a warning), removing scry-vs-scry races. Update the test count.
- [ ] **Step 6: Commit** `feat(zellij): serialize attach steering with a per-session flock`.

### Task 2: `zellij_bin` config key

**Files:**
- Modify: `src/scry/backends/zellij.py` (`ZellijBackend.__init__`), `src/scry/backends/__init__.py` (`get_backend`), `src/scry/scry.py` (`default_config`)
- Test: `tests/test_backend_zellij.py`, `tests/test_backends_factory.py`

**Interfaces:**
- Produces: `ZellijBackend(wake_keys: str = DEFAULT_WAKE_KEYS, zellij_bin: Optional[str] = None)`. When `zellij_bin` is set: `os.path.expanduser` it; if not an executable file (`os.access(p, os.X_OK)` and `os.path.isfile`), raise `ValueError(f"zellij_bin {zellij_bin!r} is not an executable file")`; otherwise use it and skip `find_bin_in_path`. `get_backend` passes `cfg.get("zellij_bin")`. `default_config["zellij_bin"] = None`.

- [ ] **Step 1: Failing tests:** `test_zellij_bin_overrides_path_lookup` (tmp executable file; `find_bin_in_path` mock not called; `backend._bin == str(path)`), `test_zellij_bin_tilde_expanded` (`monkeypatch.setenv("HOME", tmp_path)`, value `"~/z"`), `test_zellij_bin_missing_raises_value_error`, and in the factory tests `test_get_backend_passes_zellij_bin`.
- [ ] **Step 2: Run** `uv run pytest tests/test_backend_zellij.py tests/test_backends_factory.py -v` — FAIL.
- [ ] **Step 3: Implement.** The existing `try: backend = get_backend(config) except ValueError` in `scry.py` already turns the error into a clean exit.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Docs.** README "Configuration File": document `zellij_bin` (optional; for when zellij isn't on `PATH`, e.g. non-interactive ssh). CLAUDE.md: mention `zellij_bin` in the backend-abstraction bullet; test count.
- [ ] **Step 6: Commit** `feat(zellij): add zellij_bin config key`.

### Task 3: Literal rendering of window names and messages

**Files:**
- Modify: `src/scry/scry.py` (`format_window_strings`, `setup_display`; add `display_text`)
- Test: `tests/test_display.py`

**Interfaces:**
- Produces: `display_text(s: str) -> str` — replaces every character that is not `ch.isprintable()` (newlines, ESC, other controls) with `"?"`. Returns **unescaped** text; callers escape with `rich.markup.escape` **after** elision and width math.
- `format_window_strings` order per name: `display_text` → prefix-collapse → `format_window_name` (elide) → pad width computed from the elided plain text → append `escape(elided)`. Width math must never count escape backslashes.
- `setup_display` prints the error as `console.print("Error: " + escape(display_text(error_message)))`.

- [ ] **Step 1: Failing tests** in `tests/test_display.py`. Render each string with a real `Console(file=io.StringIO(), width=200, color_system=None)` and `console.print(s, end="")`, then compare plain output:
  - `test_bracket_name_renders_literally` — name `"[/]logs"` → output contains `"[/]logs"`, no `MarkupError`.
  - `test_markup_like_name_renders_literally` — `"[bold]x"` → output contains `"[bold]x"`.
  - `test_control_chars_replaced` — `"a\nb\x1b[31mc"` → contains `"a?b?[31mc"` and no `"\n"` / `"\x1b"`.
  - `test_unicode_and_leading_dash_names` — `"-dash"`, `"日本語"` render unchanged.
  - `test_escaped_name_keeps_column_width` — names `"[x]"` and `"abc"` produce rendered strings of equal plain length.
  - `test_error_message_with_brackets_renders` — `setup_display(console, [], "bad [/] thing")` with a real console (patch `sleep`) → output contains `"Error: bad [/] thing"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_display.py -v` — FAIL (MarkupError / mismatch).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Docs.** CLAUDE.md `draw_table_windows()` bullet: names render literally (escaped; control chars shown as `?`). Test count.
- [ ] **Step 6: Commit** `fix: render window names and error messages literally, not as Rich markup`.

---

## Phase 2 — Remote side (headless `remote-*` subcommands)

### Task 4: Protocol module and shared config-file reader

**Files:**
- Create: `src/scry/remote/__init__.py` (docstring only), `src/scry/remote/protocol.py`, `src/scry/config_file.py`
- Modify: `src/scry/scry.py` (`parse_args_and_configure` uses `read_config_file`)
- Test: `tests/test_remote_protocol.py`, `tests/test_config.py` (existing tests must still pass)

**Interfaces:**
- Produces (`scry.config_file`):
  - `CONFIG_PATH = "~/.scry.yml"`
  - `read_config_file(path: str = CONFIG_PATH) -> Dict` — `expanduser`; `{}` if missing or empty; YAML otherwise (errors propagate as today). `parse_args_and_configure` keeps calling `os.path.exists` on the expanded path via this function so `tests/test_config.py` patches of `scry.scry.os.path.exists` still work — if they don't, update those patches to `scry.config_file.os.path.exists`.
- Produces (`scry.remote.protocol`): the constants in "Fixed values", plus
  - `class ProtocolError(Exception)`
  - `frame(payload: Dict) -> str` — `f"{LIST_BEGIN}\n{json.dumps(payload)}\n{LIST_END}\n"`.
  - `unframe(stdout: bytes) -> Dict` — raises `ProtocolError("response too large")` if `len(stdout) > MAX_RESPONSE_BYTES`; decodes with `errors="replace"`; finds the **first** line equal to `LIST_BEGIN` (after `.strip()`) and the next line equal to `LIST_END`; `ProtocolError("no framed response")` if either is missing; `ProtocolError(f"invalid JSON: {e}")` if the text between doesn't parse to a dict.

- [ ] **Step 1: Failing tests** in `tests/test_remote_protocol.py`: `test_frame_unframe_roundtrip`; `test_unframe_ignores_noise_outside_sentinels` (banner lines before and after); `test_unframe_ignores_invalid_utf8_noise` (`b"\xff\xfe motd\n" + frame(...).encode() + b"\xc3\n"`); `test_unframe_missing_begin_raises`; `test_unframe_missing_end_raises` (`frame(...)` with the last line cut off); `test_unframe_invalid_json_raises`; `test_unframe_oversize_raises` (`b"x" * (MAX_RESPONSE_BYTES + 1)`); `test_exit_codes_distinct_and_clear_of_reserved` (the seven codes are distinct and none is in `{0, 1, 2, 126, 127, 255}` or `>= 128`).
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_protocol.py -v` — FAIL (module missing).
- [ ] **Step 3: Implement** both modules; switch `parse_args_and_configure` to `cfg.update(read_config_file())`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(remote): add list framing, exit codes, and shared config-file reader`.

### Task 5: Session identity from `/proc`

**Files:**
- Create: `src/scry/remote/identity.py`
- Test: `tests/test_remote_identity.py`

**Interfaces:**
- Produces:
  - `class IdentityError(Exception)`
  - `@dataclass(frozen=True) class SessionIdentity: pid: int; starttime: int; started_at: str` with property `token -> str` returning `f"{pid}:{starttime}"`.
  - `find_session_identity(session: str, proc_root: str = "/proc") -> SessionIdentity`
    - Scan numeric dirs in `proc_root`; read `cmdline` (NUL-split, trailing empty dropped); skip unreadable/vanished entries silently.
    - Match **exactly**: `len(argv) >= 3 and argv[1] == "--server" and os.path.basename(argv[2]) == session`. Never look at `argv[0]`; never substring-match.
    - 0 matches → `IdentityError(f"no zellij server for session {session}")`; >1 → `IdentityError("ambiguous: …pids…")`.
    - Starttime: read `stat`, `rest = data[data.rindex(")") + 2:].split()`, `starttime = int(rest[19])` (field 22 overall). Any read/parse error → `IdentityError`.
    - `started_at`: `btime` from `<proc_root>/stat` + `starttime / os.sysconf("SC_CLK_TCK")`, formatted `time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(t))`; on failure `"unknown"` (debug aid only, never fails the identity).

- [ ] **Step 1: Failing tests** in `tests/test_remote_identity.py`, building a fake proc tree in `tmp_path` with a helper `_proc(tmp_path, pid, argv, comm="zellij", starttime=12345)` that writes `cmdline` and `stat` (`f"{pid} ({comm}) S " + " ".join(["0"] * 18) + f" {starttime} 0 0\n"` — check `rest[19]` lands on starttime), plus `tmp_path/"stat"` with `btime 1700000000`:
  - `test_finds_server_by_exact_argv` — server `["/opt/zellij", "--server", "/run/user/1000/zellij/0.45.1/main"]` → `token == "101:12345"`.
  - `test_ignores_clients_and_shells` — also add `["zellij", "attach", "main"]`, `["bash", "-c", "zellij --server main"]`, `["zellij", "--server", "/x/main-old"]` → still the one server.
  - `test_missing_server_raises`, `test_ambiguous_raises` (two servers for `main`).
  - `test_comm_with_spaces_and_parens` — `comm="zel (lij) x"` → starttime parsed correctly.
  - `test_unreadable_stat_raises` (server's `stat` missing).
  - `test_vanished_process_skipped` (a pid dir with no `cmdline` file alongside the real server).
  - `test_started_at_formatting` — non-empty and not `"unknown"` with the fake `btime`.
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_identity.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(remote): identify zellij sessions by server pid and start time`.

### Task 6: Headless remote dispatch, `remote-list`, `remote-attach`

**Files:**
- Create: `src/scry/remote/server.py`
- Modify: `src/scry/__main__.py`
- Test: `tests/test_remote_server.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `read_config_file` (Task 4), protocol constants/`frame` (Task 4), `find_session_identity`/`IdentityError` (Task 5), `ZellijBackend(wake_keys, zellij_bin)` (Task 2) — note `ZellijBackend.list_sessions()` already drops EXITED sessions.
- Produces:
  - `run_scry()` in `__main__.py`: if `sys.argv[1:2]` and `sys.argv[1].startswith("remote-")` → `from scry.remote.server import main; sys.exit(main(sys.argv[1:]))`; otherwise `from scry.scry import do_table_loop; do_table_loop()`. The module-level `from scry.scry import do_table_loop` is **removed** (it would parse argv and build a backend at import).
  - `server.main(argv: List[str], proc_root: str = "/proc", out: TextIO = sys.stdout) -> int` — argparse with subcommands `remote-list --proto=INT` and `remote-attach --tab=STR --expect-session=STR --proto=INT` (all required). Missing/unknown args exit 2 via argparse — that's the "too old" signal the local side maps.
  - `server.load_remote_config(path: str = CONFIG_PATH) -> Tuple[Dict, Optional[str]]` — returns `(cfg, error)`; `error` is set when `cfg.get("backend") != "zellij"` (`"remote backend must be zellij"`) or `"zellij_wake_keys"` is absent **from the file** (`"zellij_wake_keys must be set in the remote ~/.scry.yml"`).
  - Logging setup in `main`: remove root handlers; if `cfg.get("debug")` log to `cfg.get("log_file", "/tmp/scry.log")`, else add `logging.NullHandler()`. Nothing logs to stderr.
  - **remote-list** order: proto mismatch → `frame({"proto": PROTO_VERSION, "status": "error", "error": "protocol mismatch"})`, return `EXIT_PROTO`; config error → framed `status: "error"` with the message, return `EXIT_CONFIG`; backend construction `ValueError` → same with `EXIT_CONFIG`; session not in `backend.list_sessions()` → framed `{"proto", "status": "no_session", "session"}`, return `0`; `IdentityError` → framed error, return `EXIT_IDENTITY`; else framed `{"proto": 1, "status": "ok", "session": name, "identity": token, "started_at": str, "tabs": [{"id": int, "name": str, "clients": int}, …]}` from `backend._list_tabs` (`clients = len(other_focused_clients)`), return `0`. Any other exception → framed error, `EXIT_FAILURE`.
  - **remote-attach** order: install `signal.signal(SIGINT, handler)` where handler raises `SystemExit(EXIT_FAILURE)`; proto → `EXIT_PROTO`; config/backend → `EXIT_CONFIG`; no live session → `EXIT_NO_SESSION` (**before** any `zellij attach`; never `ensure_session_group`); `IdentityError` → `EXIT_IDENTITY`; `token != expect_session` → `EXIT_STALE`; no tab with `str(tab_id) == tab` → `EXIT_TAB_GONE`; then `backend.attach_window(tab, session)`; a `RuntimeError` containing `"no longer exists"` → `EXIT_TAB_GONE`, other `RuntimeError` → `EXIT_FAILURE`; success → `0`. Prints nothing (`ssh -t` output would be wiped by the redraw).

- [ ] **Step 1: Failing tests** in `tests/test_remote_server.py`. Patch `scry.remote.server.read_config_file`, `scry.remote.server.ZellijBackend` (a `MagicMock` whose `list_sessions`/`_list_tabs` return fixtures), and `scry.remote.server.find_session_identity`. Parse output with `unframe(out.getvalue().encode())`.
  - `test_list_ok_payload` — two tabs → status ok, identity token, tabs with int ids and client counts, `proto == 1`.
  - `test_list_no_session_and_exited_session` — `list_sessions` returns `[]` → status `no_session`, exit 0.
  - `test_list_identity_unavailable` → status error, `EXIT_IDENTITY`.
  - `test_list_not_zellij`, `test_list_wake_keys_unset` (backend zellij but no wake key in file) → `EXIT_CONFIG`, `ZellijBackend` never constructed.
  - `test_list_proto_mismatch` (`--proto=99`) → `EXIT_PROTO`, payload `proto == 1`.
  - `test_attach_stale_identity` → `EXIT_STALE`, `attach_window` not called.
  - `test_attach_tab_gone` → `EXIT_TAB_GONE`.
  - `test_attach_no_session` → `EXIT_NO_SESSION`, `attach_window` and `ensure_session_group` not called.
  - `test_attach_success_calls_attach_window` → `attach_window("5", "main")`, exit 0.
  - `test_attach_sigint_handler_exits_with_failure_code` — after `main` installs it, `signal.getsignal(SIGINT)` raises `SystemExit(EXIT_FAILURE)` when called (restore the original handler in the test's `finally`).
  - `test_remote_dispatch_is_headless` — `ensure_session_group` never called in any test above (assert on the mock in a shared fixture teardown).
  - `test_no_stderr_logging` — after `main`, root logger has no `StreamHandler` to stderr (`capsys.readouterr().err == ""` after an error path).
  - `test_bad_args_exit_2` — `main(["remote-attach"])` raises `SystemExit` with code 2.
  - In `tests/test_main.py`: replace the two tests: `test_importing_main_does_not_import_scry_scry_side_effects` (import with `sys.argv = ["scry", "remote-list", "--proto=1"]` doesn't call `scry.scry.do_table_loop`), `test_run_scry_invokes_loop_once` (patch `scry.scry.do_table_loop`), `test_run_scry_routes_remote_subcommands` (patch `scry.remote.server.main` returning 83 → `SystemExit(83)`, and `scry.scry.do_table_loop` not called).
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_server.py tests/test_main.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Manual check** on this machine with a throwaway session (stock keybindings, `ZELLIJ_CONFIG_DIR` an empty temp dir) and a temp config via `HOME=<tmpdir>` containing `backend: zellij`, `session_group: scry-test-<pid>-0`, `zellij_wake_keys: "\x07\x07"`: `HOME=<tmpdir> .venv/bin/scry remote-list --proto=1` prints framed JSON with status ok. Kill and delete the session afterwards.
- [ ] **Step 6: Commit** `feat(remote): headless remote-list and remote-attach subcommands`.

---

## Phase 3 — Local config and cache

### Task 7: `remotes` validation and remote command quoting

**Files:**
- Create: `src/scry/remote/config.py`
- Modify: `src/scry/scry.py` (`default_config`, validation after `parse_args_and_configure`)
- Test: `tests/test_remote_config.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class RemoteConfig: key: str; label: str; host: str; scry: str = "scry"`
  - `parse_remotes(raw: Any) -> List[RemoteConfig]` — `None` → `[]`; must be a list of dicts; `key` a single ASCII letter (`re.fullmatch(r"[A-Za-z]", key)`), unique (case-sensitive); `host` a non-empty str not starting with `-`; `label` optional (defaults to `host`); `scry` optional, must be `"scry"`, start with `/`, or start with `~/`. Raises `ValueError` with a message naming the entry (e.g. `"remotes[1]: duplicate key 'b'"`). A key in `BARE_COMMANDS = set("sqzudlnr")` logs a WARNING, not an error.
  - `remote_command(scry: str, args: List[str]) -> str` — `"~/rest"` → `'"$HOME"/' + shlex.quote(rest)`; otherwise `shlex.quote(scry)`; then `" ".join` with `shlex.quote(a)` for each arg.
  - `default_cache_dir() -> str` — per Fixed values.
  - `validate_remote_settings(cfg: Dict) -> None` — sets `cfg["remotes"] = parse_remotes(cfg.get("remotes"))`; `remote_cache_dir` (None → `default_cache_dir()`, else `expanduser`); `remote_timeout` a positive number; `remote_ctrl_c_guard` a bool. Raises `ValueError`.
  - `default_config` gains `"remotes": None, "remote_cache_dir": None, "remote_timeout": 10, "remote_ctrl_c_guard": True`.
  - In `scry.py`, call `validate_remote_settings(config)` inside the existing import-time `try` (extend it to cover both calls), so errors exit 1 with `ERROR: <message>`, like an invalid backend.

- [ ] **Step 1: Failing tests:** valid two-remote config parses; `test_key_must_be_single_letter` (`"bb"`, `"1"`, `""`); `test_duplicate_key`; `test_host_leading_dash`; `test_bare_command_key_warns` (`"z"` parses, WARNING in `caplog`); `test_scry_path_policy` (accept `"scry"`, `"/opt/scry"`, `"~/.local/bin/scry"`; reject `"bin/scry"`, `"~user/scry"`); `test_remote_command_tilde_expands_remotely` (`remote_command("~/.local/bin/scry", ["remote-list", "--proto=1"]) == '"$HOME"/.local/bin/scry remote-list --proto=1'`); `test_remote_command_quotes_awkward_values` (`"/opt/my scry"`, arg `"--expect-session=1:2 ;rm"` → round-trips through `shlex.split` to the original values, with `"$HOME"` handled as above); `test_default_cache_dir_xdg` and fallback; `test_validate_timeout_and_guard_types`; `test_invalid_remotes_exit_cleanly` (subprocess: `python -c "import scry.scry"` with `HOME` set to a tmp dir whose `.scry.yml` has a duplicate key → exit 1, stderr contains `"duplicate key"`, no `"Traceback"`).
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_config.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(remote): validate remotes config and quote remote commands`.

### Task 8: Per-host cache files and the section model

**Files:**
- Create: `src/scry/remote/cache.py`
- Test: `tests/test_remote_cache.py`

**Interfaces:**
- Consumes: `RemoteConfig` (Task 7).
- Produces:
  - `RemoteTabRef(NamedTuple): host: str; identity: str; tab_id: str` — the history entry and the `window_id` of remote rows.
  - `@dataclass class RemoteSection: remote: RemoteConfig; state: str  # "ok" | "failed" | "no_session" | "never"; last_success: Optional[float]; reason: str; rows: List[Dict[str, Any]]` — each row `{"window_id": RemoteTabRef, "window_name": str, "window_active_clients": str}`, sorted by `(name, int(tab_id))`.
  - `cache_path(cache_dir: str, host: str) -> str` — `re.sub(r"[^A-Za-z0-9._-]", "_", host) + ".json"`.
  - `read_cache(cache_dir: str, host: str) -> Optional[Dict]` — `None` on missing, empty, unreadable, non-JSON, or non-dict. Never raises.
  - `write_cache(cache_dir: str, host: str, data: Dict) -> None` — `os.makedirs(cache_dir, exist_ok=True)`; `tempfile.NamedTemporaryFile("w", dir=cache_dir, prefix=".tmp-", delete=False)`; write, flush, `os.replace` onto `cache_path`; remove the temp file if anything fails, then re-raise `OSError`.
  - Cache file schema: `{"host", "session", "identity", "started_at", "tabs": [{"id": int, "name": str, "clients": int}], "last_success": float | None, "last_attempt": {"status": "ok" | "failed" | "no_session", "reason": str, "at": float}}`.
  - `apply_result(old: Optional[Dict], host: str, result: "RefreshResult", now: float) -> Dict` — `ok`: replace session/identity/started_at/tabs, `last_success = now`; `no_session`: clear tabs and identity, keep `last_success`; `failed`: keep everything, set `last_attempt` only. (`RefreshResult` is defined in Task 9; import it under `TYPE_CHECKING` or define it here — **define it here** to avoid a cycle: `@dataclass class RefreshResult: status: str; reason: str = ""; payload: Optional[Dict] = None`.)
  - `load_sections(remotes: List[RemoteConfig], cache_dir: str) -> List[RemoteSection]` — config order; `read_cache` → `None` gives `state="never"`; rows only when an identity is present.

- [ ] **Step 1: Failing tests:** `test_cache_path_sanitized` (`"coder.build"` → `coder.build.json`; `"user@h:22"` → `user_h_22.json`); `test_read_missing_empty_corrupt_returns_none` (three files); `test_write_cache_creates_dir`; `test_write_cache_unwritable_raises_oserror` (dir `chmod 0o500`; skip if `os.geteuid() == 0`); `test_write_cache_is_atomic_and_leaves_no_temp_files` (two threads writing 50 times each while a third reads 100 times; every read is `None`-free and a dict; afterwards only `<host>.json` in the dir); `test_apply_ok_replaces`; `test_apply_no_session_clears_tabs_keeps_last_success`; `test_apply_failed_keeps_tabs`; `test_load_sections_sorted_by_name_then_id` (names `["b","a","a"]` ids `[3,9,2]` → order `a(2), a(9), b(3)`); `test_load_sections_states` (never / ok / failed / no_session); `test_letter_reassignment_reads_other_file` (cache for `coder.old`; config `b → coder.new` → `state == "never"`).
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_cache.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(remote): per-host atomic cache files and remote section model`.

### Task 9: `z` — parallel refresh

**Files:**
- Create: `src/scry/remote/refresh.py`, `src/scry/remote/messages.py`
- Test: `tests/test_remote_refresh.py`

**Interfaces:**
- Consumes: `RemoteConfig`, `remote_command` (Task 7); `RefreshResult`, `read_cache`, `write_cache`, `apply_result` (Task 8); `unframe`, `ProtocolError`, `PROTO_VERSION`, exit codes (Task 4).
- Produces:
  - `src/scry/remote/messages.py`: `exit_message(code: int, key: str) -> str` with exactly:
    - `255` → `f"host {key} unavailable"`
    - `127` → the text: scry not found on host {key}; set ``scry:`` in remotes (backticks around `scry:` are part of the message)
    - `2` → `f"scry on host {key} is too old for remote mode"`
    - `EXIT_STALE` → `f"host {key}'s tab list is stale — press z"`
    - `EXIT_TAB_GONE` → `f"that tab is gone on host {key} — press z"`
    - `EXIT_NO_SESSION` → `f"no zellij session running on host {key}"`
    - `EXIT_CONFIG` → `f"scry on host {key} isn't set up for remote mode (needs backend: zellij and zellij_wake_keys in its ~/.scry.yml)"`
    - `EXIT_IDENTITY` → `f"couldn't identify the zellij session on host {key}"`
    - `EXIT_PROTO` → `f"scry on host {key} is incompatible — upgrade it"`
    - `-signal.SIGINT` → `f"connection to host {key} cancelled"`
    - anything else nonzero → `f"remote command on host {key} failed (exit {code})"`; `0` → `""`.
  - `refresh_command(remote: RemoteConfig) -> List[str]` — `["ssh", "-T", "-o", "BatchMode=yes", remote.host, remote_command(remote.scry, ["remote-list", f"--proto={PROTO_VERSION}"])]`.
  - `parse_refresh(remote: RemoteConfig, returncode: int, stdout: bytes, stderr: bytes) -> RefreshResult` — try `unframe` first (a framed payload wins over the exit code): payload `proto != PROTO_VERSION` → `failed`, `exit_message(EXIT_PROTO, key)`; `status == "ok"` → `ok` with payload; `"no_session"` → `no_session`; `"error"` → `failed` with the payload's `error`. On `ProtocolError`: if `returncode in (255, 127, 2)` → `failed` with `exit_message`; for 255 append `"; run 'ssh <host>' by hand once"` when stderr mentions `"Permission denied"` or `"Host key verification failed"`; otherwise `failed` with `f"bad response from host {key}: {err}"`.
  - `refresh_remotes(remotes: List[RemoteConfig], cache_dir: str, timeout: float, guard: bool) -> str` — returns the message for the prompt slot: `"no remotes configured"` if empty; `"refresh cancelled"` on Ctrl-C (guard on); otherwise `"; ".join(f"{key}: {reason}" for failed hosts)` or `""`.
    - One `subprocess.Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, start_new_session=True)` per host, each drained by a worker thread calling `proc.communicate(timeout=deadline - now)`. On `TimeoutExpired`: `os.killpg(proc.pid, SIGKILL)` (ignore `ProcessLookupError`), then `communicate()` to reap, result `failed`, `f"timed out after {timeout:g}s"`.
    - Main thread waits with `_wait_workers(threads: List[threading.Thread]) -> None` (joins in a loop with `join(0.1)` so SIGINT is delivered).
    - If `guard`: `KeyboardInterrupt` around `_wait_workers` → `killpg` every still-running proc, join workers, write **no** cache files, return `"refresh cancelled"`. If not `guard`: no `try`, the interrupt propagates.
    - After all workers finish: for each host `write_cache(cache_dir, host, apply_result(read_cache(...), host, result, time.time()))`; an `OSError` adds `f"{key}: cannot write cache ({e.strerror})"` to the message instead of raising.

- [ ] **Step 1: Failing tests.** Patch `scry.remote.refresh.subprocess.Popen` with a `FakeProc` class (`pid`, `communicate(timeout=None)` returning preset `(stdout, stderr)` or raising `subprocess.TimeoutExpired` once, `returncode`), and patch `os.killpg`:
  - `test_refresh_command_argv` — exactly `["ssh", "-T", "-o", "BatchMode=yes", "coder.build", "scry remote-list --proto=1"]`; Popen called with `stdin=subprocess.DEVNULL`, `start_new_session=True`.
  - `test_parse_refresh_ok_no_session_error_proto` (four cases).
  - `test_parse_refresh_framed_wins_over_exit_code` (payload ok, returncode 1 → ok).
  - `test_parse_refresh_exit_codes` — 255, 127, 2 with empty stdout → the three spec messages.
  - `test_parse_refresh_255_auth_hint`.
  - `test_refresh_writes_each_host` — two hosts, one ok, one 255 → both cache files written, message `"c: host c unavailable"`.
  - `test_refresh_timeout_kills_group` — `killpg(pid, SIGKILL)` called; message contains `"timed out after 10s"`.
  - `test_refresh_timeout_mid_output_keeps_tabs` — existing cache with tabs; proc returns `LIST_BEGIN + "\n{\"proto\": 1"` (no END) → cache tabs unchanged, `last_attempt.status == "failed"`.
  - `test_refresh_runs_hosts_in_parallel` — two fakes each sleeping 0.3 s in `communicate` → total < 0.5 s.
  - `test_refresh_ctrl_c_kills_workers_keeps_cache` — patch `_wait_workers` to raise `KeyboardInterrupt` → returns `"refresh cancelled"`, `killpg` called for each pid, cache file bytes unchanged.
  - `test_refresh_ctrl_c_unguarded_propagates` — `guard=False` → `pytest.raises(KeyboardInterrupt)`.
  - `test_refresh_unwritable_cache_reports_message` — `write_cache` raises `PermissionError` → message contains `"cannot write cache"`, no raise.
  - `test_refresh_no_remotes`.
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_refresh.py -v` — FAIL.
- [ ] **Step 3: Implement** `messages.py` and `refresh.py`.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Verify on a real host — does killing a timed-out refresh's process group also kill a `ControlPersist` master it just started?** Using `localhost` (or a real Coder host if available), with a scratch ControlPath so the user's own masters are untouched:
  ```bash
  CP=/tmp/claude-scry-cm-$$; python3 - <<EOF
  import os, signal, subprocess, time
  p = subprocess.Popen(["ssh", "-o", "ControlMaster=auto", "-o", "ControlPath=$CP", "-o", "ControlPersist=60",
                        "-T", "localhost", "sleep 30"], stdin=subprocess.DEVNULL, start_new_session=True)
  time.sleep(3); os.killpg(p.pid, signal.SIGKILL); p.wait()
  EOF
  ssh -o ControlPath=$CP -O check localhost; echo "check exit: $?"; ssh -o ControlPath=$CP -O exit localhost
  ```
  Expected outcome A: `Master running` (exit 0) → the master escaped the group; nothing to do, note it in the commit message. Outcome B: check fails → the master was killed. **Stop and ask the user** whether to spare it (e.g. SIGKILL only `proc.pid` plus the ProxyCommand children found via `/proc/<pid>/task/*/children`) or accept the cost; don't pick silently. If `ssh localhost` needs interactive auth, ask the user to run it or provide a host.
- [ ] **Step 6: Commit** `feat(remote): z refreshes all remotes in parallel with per-host timeout`.

---

## Phase 4 — Index and commands

### Task 10: Remote sections in the index

**Files:**
- Modify: `src/scry/scry.py` (`draw_table_windows`, `format_window_strings`, `setup_display`, `OPTION_HELP` / prompt, `show_help`; add `draw_remote_section`, `section_header`, `format_age`, `prompt_hint`)
- Test: `tests/test_display.py`, `tests/test_remote_display.py`

**Interfaces:**
- Consumes: `RemoteSection`, `RemoteTabRef` (Task 8); `display_text` (Task 3).
- Produces:
  - `format_age(seconds: float) -> str` — `< 60` → `"<1m ago"`, `< 3600` → `f"{m}m ago"`, `< 86400` → `f"{h}h ago"`, else `f"{d}d ago"`.
  - `section_header(section: RemoteSection, width: int, now: float) -> rich.text.Text` — plain `Text` (no markup parsing), dim style when `state == "failed"`: left `f"── {key} · {display_text(label)} "`, right per state — ok `format_age(now - last_success)`; failed `"unavailable · " + format_age(...)` (or `"unavailable"` if never succeeded); no_session `"no session running — start it, then press z"`; never `"never refreshed — press z"`; filled with `─` to `width`.
  - `format_window_strings(column_width, windows, items_per_col, labels: Optional[List[str]] = None, dim: bool = False) -> List[str]` — when `labels` is given, the index column shows `labels[i]` right-justified to `max(len(l) for l in labels)` instead of `i`; `dim` prefixes `"[dim]"`. History highlighting compares `window["window_id"]` to `WINDOW_HISTORY` exactly as today, so `RemoteTabRef` ids highlight with no extra code. `hide_prefixes` and elision apply unchanged.
  - `draw_table_windows(console, windows, sections: Optional[List[RemoteSection]] = None) -> int` — local block as today, then for each section: blank line, `console.print(section_header(...))`, then its rows in the same column layout (labels `f"{key}{n}"`, `dim=section.state == "failed"`); returns total lines printed.
  - `setup_display(console, windows, error_message, sections=None)` passes sections through. `console.line(...)` gets `max(0, height - lines - 2)`.
  - `prompt_hint(n_local: int, sections: List[RemoteSection]) -> str` — e.g. `"0-7/b0-b3/n/r/s/u/d/l/z/q/?"`: local range (`"##"` when 0 windows), each section with rows as `f"{k}0-{k}{m-1}"`, then `n/r/s/u/d/l`, `z` only if remotes are configured, `q/?`. `do_table_loop` uses it instead of joining `OPTION_HELP` keys.
  - `show_help(console, remotes_configured: bool = False)` — when true, also prints: `<key><n>` `"Attach remote tab (e.g. b2); # is as of the last z"`, `z` `"Refresh remote tabs (blocks until each host answers or times out)"`, and `"Remote attach stuck? Ctrl-C while connecting, or type ~. to drop the ssh connection"`.
- Empty local list: `do_table_loop` still `continue`s after creating the session group; the next iteration lists the new session and draws remote sections. No change; add a comment there saying so.

- [ ] **Step 1: Failing tests** in `tests/test_remote_display.py` (real `Console(file=StringIO(), width=120, color_system=None)`, patch `get_terminal_size` to 120 columns): `test_format_age_buckets`; `test_header_ok`, `test_header_failed_dim`, `test_header_no_session`, `test_header_never`; `test_header_label_with_markup_literal` (label `"[red]x"`); `test_remote_rows_labelled_b0_b1_in_name_order`; `test_remote_row_hash_from_cached_clients`; `test_remote_history_highlight` (history `[RemoteTabRef("h","1:2","5")]` → that row starts with `"[bold reverse magenta]"`); `test_remote_name_with_newline_and_brackets_renders`; `test_unavailable_rows_dim`; `test_prompt_hint_examples` (`(8, [b with 4 rows, c with 0 rows])` → `"0-7/b0-b3/n/r/s/u/d/l/z/q/?"`; no remotes → no `z`); `test_setup_display_overflowing_height_does_not_raise` (console height 10, 3 sections × 20 rows); `test_help_mentions_remote_escapes_only_when_configured`.
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_display.py tests/test_display.py -v` — FAIL.
- [ ] **Step 3: Implement.** Keep existing call sites working with the new optional params.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(remote): draw remote sections in the index`.

### Task 11: `<key><n>`, `z`, snapshot selection, and history

**Files:**
- Modify: `src/scry/scry.py` (`process_command`, `do_table_loop`; add `history_entry_present`)
- Test: `tests/test_commands.py`, `tests/test_window_history.py`

**Interfaces:**
- Consumes: `RemoteSection`, `RemoteTabRef`, `load_sections` (Task 8); `refresh_remotes` (Task 9); `prompt_hint`, `show_help` (Task 10).
- Produces:
  - `process_command(command, windows, session_group, console, sections: Optional[List[RemoteSection]] = None) -> Tuple[Union[str, RemoteTabRef, None], str]`:
    - `re.fullmatch(r"([A-Za-z])(\d+)", command)` → look up the section with that key **in `sections`** (the drawn snapshot); unknown key or out-of-range → `(None, "Invalid index")`; else the row's `window_id` (a `RemoteTabRef`). `"b"` alone falls through to the dispatch table / "not recognized".
    - `"z"` → `(None, refresh_remotes(config["remotes"], config["remote_cache_dir"], config["remote_timeout"], config["remote_ctrl_c_guard"]))`.
    - `""` and `"s"` return history entries as today; they may be `RemoteTabRef`s.
  - `history_entry_present(entry, windows, sections) -> bool` — `RemoteTabRef`: any section row whose `window_id == entry`; otherwise any local `window_id == entry`. `do_table_loop`'s cleanup uses it.
  - `do_table_loop` per iteration: `windows = backend.list_windows(...)`; `sections = load_sections(config["remotes"], config["remote_cache_dir"])` (local disk only); cleanup; `setup_display(..., sections)`; prompt with `prompt_hint`; `process_command(..., sections)`; dispatch: `RemoteTabRef` → `attach_remote(...)` (Task 12; until then return `"remote attach not implemented"` from a stub in `scry.remote.attach`), on `""` result `update_window_history(target)`; `str` → local attach as today.

- [ ] **Step 1: Failing tests:** `test_remote_index_resolves` (`"b1"` → second row's ref); `test_remote_bare_key_not_a_selection` (`"b"` → `'command "b" not recognized'`); `test_remote_unknown_key_or_range` (`"x0"`, `"b9"` → `"Invalid index"`); `test_z_calls_refresh_with_config` (patch `scry.scry.refresh_remotes`); `test_selection_uses_drawn_snapshot` — build `sections` from a cache file, then rewrite the cache file with different tabs, then `process_command("b0", …, sections)` returns the **original** ref; `test_enter_and_swap_return_remote_entries`; `test_history_cleanup_remote_present` / `_absent_after_identity_change` (same host and tab id, different identity → absent); `test_local_and_remote_ids_dont_collide` (local zellij id `"5"` vs `RemoteTabRef("h","1:2","5")` → distinct history entries).
- [ ] **Step 2: Run** `uv run pytest tests/test_commands.py tests/test_window_history.py -v` — FAIL.
- [ ] **Step 3: Implement**, including the `attach_remote` stub so the loop is importable and testable.
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(remote): select remote tabs by <key><n> and refresh with z`.

---

## Phase 5 — Attach over ssh

### Task 12: `attach_remote` — ssh, Ctrl-C guard, terminal restore

**Files:**
- Modify: `src/scry/remote/attach.py` (replace the stub), `src/scry/remote/messages.py` (only if Step 6 changes the 255 text), `src/scry/scry.py` (`do_table_loop` dispatch)
- Test: `tests/test_remote_attach.py`

**Interfaces:**
- Consumes: `RemoteTabRef` (Task 8), `RemoteConfig`, `remote_command` (Task 7), `exit_message` (Task 9), `PROTO_VERSION`.
- Produces:
  - `RESET_SEQUENCE` (Fixed values).
  - `attach_command(remote: RemoteConfig, ref: RemoteTabRef) -> List[str]` — `["ssh", "-t", remote.host, remote_command(remote.scry, ["remote-attach", f"--tab={ref.tab_id}", f"--expect-session={ref.identity}", f"--proto={PROTO_VERSION}"])]`.
  - `@contextmanager sigint_guard(enabled: bool) -> Iterator[None]` — if enabled: `prev = signal.signal(SIGINT, _noop)` where `def _noop(signum, frame): pass`; `finally: signal.signal(SIGINT, prev)`. If not enabled: yields, installs nothing. **Never `SIG_IGN`.**
  - `@contextmanager restored_terminal(stdin_fd: int = 0, stdout_fd: int = 1) -> Iterator[None]` — if `os.isatty(stdin_fd)`: save `termios.tcgetattr` before; `finally`: `tcsetattr(stdin_fd, TCSADRAIN, saved)` then `os.write(stdout_fd, RESET_SEQUENCE.encode())`. Not a tty → no-op.
  - `attach_remote(ref: RemoteTabRef, remotes: List[RemoteConfig], guard: bool, console: Console) -> str` — find the remote by `ref.host` (missing → `f"no remote configured for host {ref.host}"`); `console.print(Text(f"connecting to host {key}…"))`; `with restored_terminal(), sigint_guard(guard): rc = subprocess.call(attach_command(...))`; `OSError` (no ssh binary) → `f"cannot run ssh: {e.strerror}"`; return `exit_message(rc, key)` (`""` on 0).
  - `do_table_loop`: `display_error_message = attach_remote(target, config["remotes"], config["remote_ctrl_c_guard"], console)`; on `""` call `update_window_history(target)`.

- [ ] **Step 1: Failing tests** in `tests/test_remote_attach.py`:
  - `test_attach_command_argv` — `["ssh", "-t", "coder.build", "scry remote-attach --tab=5 --expect-session=101:12345 --proto=1"]`.
  - `test_exit_message_mapping` — every row of the `exit_message` table, including 127, 2, 255, `-2`, and an unknown code `3`.
  - `test_sigint_guard_child_still_dies_and_scry_survives` — **real child**: inside `sigint_guard(True)`: `p = subprocess.Popen(["sleep", "30"])`; `os.kill(p.pid, SIGINT)`; `os.kill(os.getpid(), SIGINT)`; `p.wait(5) == -signal.SIGINT`; no `KeyboardInterrupt` raised. This fails if the guard used `SIG_IGN` (the child would ignore SIGINT) — add a sanity variant `test_sig_ign_would_break_escape` that sets `SIG_IGN` around the same Popen and asserts the child is still alive after the kill (then kills it), documenting why.
  - `test_sigint_guard_restores_previous_handler` — previous handler object is back after exit, including when the body raises.
  - `test_sigint_guard_disabled_installs_nothing` — `signal.getsignal(SIGINT)` unchanged inside the block.
  - `test_restored_terminal_on_success_failure_and_exception` — patch `os.isatty` True, `termios.tcgetattr`/`tcsetattr`, and `os.write`: for rc 0, rc 255, and `subprocess.call` raising, `tcsetattr` gets the saved attrs and `os.write` gets exactly `RESET_SEQUENCE`.
  - `test_attach_remote_prints_connecting_and_maps_rc` — patch `subprocess.call` → 81; console output contains `"connecting to host b…"`; return equals the stale message.
  - `test_attach_remote_success_updates_history` — via `do_table_loop`-level dispatch helper or by asserting `attach_remote` returns `""` and the loop calls `update_window_history` (patch `Prompt.ask` to `["b0", "q"]` and `sys.exit`).
  - `test_attach_remote_guard_off_with_config` — `guard=False` → `signal.signal` never called (patch it).
- [ ] **Step 2: Run** `uv run pytest tests/test_remote_attach.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Verify on a real host — what does ssh exit with when the remote command dies by a signal?**
  ```bash
  ssh -T localhost 'kill -TERM $$'; echo "TERM: $?"
  ssh -T localhost 'kill -KILL $$'; echo "KILL: $?"
  ssh -t localhost 'sleep 30'   # press ~. ; then: echo "escape: $?"
  ```
  Expected per OpenSSH: 255 for a signal death and for `~.`. Record the observed values in the commit message.
- [ ] **Step 6: Act on Step 5.** If signal deaths or `~.` report 255, change the 255 message in `messages.py` to `f"host {key} unavailable, or the connection was closed"` and update `test_exit_message_mapping`. If they report something else (e.g. `128+n`), they already fall to the generic message; leave 255 as the spec's `"host {key} unavailable"`.
- [ ] **Step 7: Manual check** (throwaway session, `HOME=<tmpdir>` remote config as in Task 6 Step 5, local `remotes:` pointing `host: localhost`, `scry:` at an absolute wrapper script that runs `HOME=<tmpdir> exec <worktree>/.venv/bin/scry "$@"`): `z`, select `b1`, land on the right tab, detach, back at the index with the terminal sane; Ctrl-C during "connecting…" (simulate a slow connect with `ProxyCommand` `sh -c 'sleep 5; nc %h %p'` in a scratch `-F` config if needed) leaves scry running with "connection to host b cancelled". Clean up the session.
- [ ] **Step 8: Commit** `feat(remote): attach to remote tabs over ssh with Ctrl-C guard and terminal restore`.

---

## Phase 6 — Live tests and docs

### Task 13: Live tests over `ssh localhost`

**Files:**
- Create: `tests/test_remote_live.py`
- Test: itself

**Interfaces:**
- Consumes: `refresh_remotes`, `read_cache` (Tasks 8–9), `attach_command` (Task 12), the `LiveSession` pattern from `tests/test_zellij_live.py` (copy the fixture shape; don't import private helpers across test modules unless trivially shared via `conftest.py`).
- Gating: `pytest.mark.live`; skip unless `SCRY_LIVE_ZELLIJ=1`, `zellij` on PATH, and `ssh -T -o BatchMode=yes localhost true` exits 0 within 10 s.
- Setup fixture: throwaway session `scry-test-<pid>-<n>` created with `ZELLIJ_CONFIG_DIR` = empty tmp dir; a remote HOME tmp dir with `.scry.yml` (`backend: zellij`, `session_group: <that name>`, `zellij_wake_keys: "\x07\x07"`); a wrapper script `tmp/scry-remote` (`#!/bin/sh\nHOME=<remote home> exec <abs .venv/bin/scry> "$@"`, mode 0755); `RemoteConfig(key="b", label="loop", host="localhost", scry=<wrapper>)`; cache dir under `tmp_path`. Teardown: terminate hosted clients, `kill-session` + `delete-session`.

- [ ] **Step 1: Write tests:**
  - `test_live_list_ok` — create tabs `alpha`, `beta`; `refresh_remotes(...) == ""`; cache tabs names include both; identity matches `r"^\d+:\d+$"`.
  - `test_live_identity_stable_across_lists` — two refreshes → same identity.
  - `test_live_identity_changes_after_restart` — kill-session, recreate with the same name, refresh → different identity.
  - `test_live_no_session` — kill + delete the session, refresh → cache `last_attempt.status == "no_session"` and no tabs; the session is **not** recreated (`list-sessions` lacks it).
  - `test_live_stale_attach_refused` — `subprocess.run(attach_command(remote, RemoteTabRef("localhost", "1:1", "0")), stdin=DEVNULL)` with `-t` replaced by `-T` → returncode `EXIT_STALE`.
  - `test_live_attach_lands_on_tab` — host one client on tab 0; run `["ssh", "-tt", ...]` (the `attach_command` argv with `-t` → `-tt`) inside a `PtyClient` sized 24×80, draining; poll `_client_tabs` until a new client is on the target tab (`beta`'s id); then terminate it and assert the hosted client is still on tab 0.
- [ ] **Step 2: Run** `SCRY_LIVE_ZELLIJ=1 uv run pytest -m live -v` — PASS (or SKIP with the gating reason if `ssh localhost` isn't set up; then ask the user whether to set it up). Confirm `zellij list-sessions` shows no leftover `scry-test-*` sessions.
- [ ] **Step 3: Run** `uv run pytest -q` — PASS, live tests skipped.
- [ ] **Step 4: Commit** `test(remote): live remote tests over ssh localhost`.

### Task 14: README and CLAUDE.md

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: README.** Add a "Remote zellij tabs" section under Usage: what it does; the commands (`b2`, `z`); index headers and what "unavailable" / "no session running" / "never refreshed" mean; `#` is as of the last `z`; escapes (Ctrl-C while connecting, `~.`); the 7-item **Setup checklist** copied from the spec verbatim in substance (including the `Host coder.*` ControlMaster block, `mkdir -p ~/.ssh/cm && chmod 700 ~/.ssh/cm`, `ssh -O check`/`-O exit`, the `TERM`/terminfo check, and `ssh -T -o BatchMode=yes <host> <scry> remote-list --proto=1`); the config keys `remotes`, `remote_cache_dir`, `remote_timeout`, `remote_ctrl_c_guard`, `zellij_bin`; the exit-message table in user terms; known limitations (from the spec's list, one line each). Add the new files to "Project Structure". Mention the Task 9 Step 5 ControlPersist finding if it affects users.
- [ ] **Step 2: CLAUDE.md.** Architecture: a fourth bullet for `src/scry/remote/` (`server.py`/`identity.py` remote end; `config.py`, `cache.py`, `refresh.py`, `attach.py`, `messages.py`, `protocol.py` local end) and the `__main__` routing of `remote-*` before `scry.scry` import; `scry.config_file`. Key Design Decisions: a "Remote zellij" bullet (fresh ssh per action, cache-only redraws, identity = server pid:starttime, snapshot-bound selection, history refs, exit codes 80–86, Ctrl-C guard is a no-op handler not `SIG_IGN`). Testing: new test count from `uv run pytest -q`, and the live remote tests' extra `ssh localhost` requirement.
- [ ] **Step 3: Run** `uv run ruff format . && uv run ruff check . --fix && uv run pytest -q` — clean and PASS.
- [ ] **Step 4: Commit** `docs: document remote zellij tabs`.
