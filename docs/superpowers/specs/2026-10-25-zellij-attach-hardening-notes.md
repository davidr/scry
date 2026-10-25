# Notes: zellij attach hardening (deferred)

**Date:** 2026-10-25
**Status:** Deferred. Not planned; kept for if wrong-tab landings become a real problem.
**Context:** Came out of reviewing `2026-10-25-remote-zellij-design.md` (see the
Fable and Astra reviews alongside it). Applies to **local and remote** zellij
attach, since they share `ZellijBackend._attach`.

## Decision

The user accepted **best-effort** attach: in steady state the failure cases
below are unlikely, and they'll notice if they land on the wrong tab. So:

- No strict attach; today's behavior stays (2 s hold, then release regardless).
- No plugin spike.
- One addition, in `ZellijBackend._attach` so it covers **local and remote**
  (one code path): an `fcntl.flock` on `$XDG_RUNTIME_DIR` (fallback `/tmp`)
  `scry-attach-<session>.lock`, held only for the steering phase (before
  spawning the client → director finishes). If busy, wait a few seconds, then
  proceed without it and log a warning. Removes scry-vs-scry races (option C).
- Residual risks are documented, not designed around.

## Why attach can't be made fully safe today

zellij 0.45.1 cannot target a client with remote actions; they go to the client
that most recently received key input. `list-clients` takes no options and
gives only client ID, focused pane, and running command — no PID/tty binding to
the client scry spawned. Hence:

1. **Wrong client identified.** `_wait_for_new_client` takes the first client
   ID not in the pre-attach snapshot. If another client attaches concurrently,
   scry can pick it, confirm *it* reached the tab, and release input into its
   own (undirected) client. (Astra reproduced the logic with mocked
   observations: known `{1}`, live `[1,2,3]`, client 2 on target → confirmation
   succeeds for the wrong client.)
2. **Another client steered.** If any other client receives input between the
   wake keys and `go-to-tab-by-id`, that client is moved instead; its next
   keystroke lands on the target tab. The "send it back" step is itself an
   untargeted action and can't reliably undo it.
3. **Failure paths release anyway.** `proxy` releases held I/O after
   `hold_timeout` (2 s) whether or not direction succeeded (`_proxy_loop`), and
   `_direct_client` sets the gate on every failure path (client never appears,
   `_DIRECT_ATTEMPTS` exhausted, exception), only logging a warning. Keys typed
   during the blank hold go to whatever tab the client first landed on.
   zellij 0.45.1 client-ID reuse can also leave the client undirected.
4. **Hangs.** `ZellijCmd` runs `subprocess.run()` with no timeout, and
   `_attach` does an unbounded `director.join()`; a hung zellij command can
   stall scry on detach.

## Signals that it's become a problem

- Landing on the wrong tab, or typed-ahead keys appearing in the wrong tab.
- Another terminal's zellij client jumping tabs when scry attaches.
- Run with `debug: true` and look in the log for
  `could not direct zellij client`, `zellij client did not attach`, or
  `failed directing zellij client` warnings (they're WARNING level, so they're
  invisible with debug off).

## Options, cheapest first

### A. Discard held input on failure (~20-30 lines)

Gate carries an outcome (success/failure) instead of a bare `Event`. On
failure, or if the 2 s hold expires without success, **discard held input**
instead of forwarding it; still release the screen. Optionally bound
`director.join()`. Removes the type-ahead hazard (3); leaves a visibly wrong
screen.

### B. Full strict attach (~100-150 lines + tests)

1. **Outcome gate:** pending/success/failure, first write wins under a lock, so
   a late success can't reopen an aborted gate.
2. **One overall deadline** (~8 s) passed to the director; `ZellijCmd` gains a
   `timeout` (remaining time) so no command can hang. Every failure path records
   failure; only confirmed landing records success.
3. **Proxy:** no timed release. Success → flush held output, forward held input
   (as today). Failure → discard both buffers, return; existing cleanup restores
   termios and kills the client. Final flush only if released. Client exit while
   pending = failure. `\x03` in held input (raw mode) = abort. Backstop: proxy
   self-fails shortly after the director's deadline. Cap held output (~1 MB).
4. **`_attach`:** bounded `director.join()`; on failure raise
   `RuntimeError("couldn't land on tab <id>; try again")`, shown at scry's
   prompt like "window <id> no longer exists" (remotely: a distinct exit code).
5. Tests: rewrite the timed-release test in `test_pty_client.py`; add each
   failure path, late success, Ctrl-C while held, early client exit.

Fixes (3) and (4). Does **not** fix (1) or (2).

### C. Serialize attaches

Remote-side per-host lock around steering (adopted for remote). Could be applied
locally too (a lock file in the runtime dir). Removes scry-vs-scry instances of
(1)/(2); not manual attaches or typing in other clients.

### D. Client-targeted steering via a zellij plugin (spike first)

Unknown whether the plugin API can move one specific client. The only option
that could actually fix (1) and (2). Validate with a test that keeps a
pre-existing client typing during attaches and checks **which pane receives
sentinel input** — not just what the director returns.

### E. Session per window

See `docs/zellij-session-per-window.md`. Attaching to a session needs no
steering at all, eliminating (1)-(3). A large change to how sessions are used.
