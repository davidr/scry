# Review: Remote zellij tabs in the scry index

Reviewed 2026-10-25 against the proposed design, the current implementation and tests, the installed Zellij 0.45.1 CLI, and relevant upstream source. Three delegated investigations covered attach safety, SSH/protocol behavior, and cache/UI integration. This is a design review; no implementation was changed.

**I would keep the overall architecture, but would not approve implementation of the full plan yet.** Remote scry over foreground SSH, explicit refresh, and a shared display cache fit the problem. The central difficulty is that the proposed verification does not establish the promised wrong-tab safety. Some cases can falsely succeed, rather than merely fail with “try again.” Resolve the first four findings in a small feasibility step before investing in the remote UI.

“Blocker” below means the current design cannot substantiate a core safety promise. “High” means a requirement needs to be added before implementation. “Medium” means a concrete integration or reliability issue that is fixable within the architecture.

**1. Blocker — Confirmation can identify somebody else's client as ours.**

The prerequisite at design lines 179–205 fixes failure handling, but inherits an unreliable success condition. `_wait_for_new_client()` returns the first client ID absent from a previous snapshot (`src/scry/backends/zellij.py:181–206`). `_confirm_direction()` then checks that ID's tab (`:482–494`). Neither establishes that the ID belongs to the process/PTY scry spawned.

Example: client 1 already exists; our attach creates client 3 while another terminal creates client 2. The lookup chooses 2. Client 2 types after our wake keys, making it the target of the subsequent steering action. Client 2 reaches the requested tab, while our client 3 remains elsewhere. Confirmation succeeds and a strict success-only gate still releases our input into the wrong tab.

I reproduced that false-positive logic by calling the existing methods with mocked client observations: known IDs `{1}`, live IDs `[1, 2, 3]`, requested tab 7, client 2 on tab 7, and our client 3 on tab 0. The result was `inferred_client='2', confirmation_succeeded=True`. This demonstrates the logic defect; it was not a live timing reproduction.

Rejecting multiple new IDs is insufficient: another client can appear before ours, or ours can reuse an ID from the old snapshot. Serializing scry attaches also does not exclude ordinary manual attaches. The existing `list-clients` output provides client ID, focused pane ID, and running command, not an owned-client PID/TTY binding; the current wrapper retains only the ID (`:172–175`).

**Required change:** establish a verifiable association between the owned client and the confirmation result. Make that a feasibility requirement, not a builder detail. If the supported CLI cannot provide it, investigate a client/plugin/upstream interface that can, or explicitly revise the safety requirement. Changing timeout behavior alone cannot deliver it.

**2. Blocker — Attaching can redirect an already active terminal's keystrokes.**

The gate protects only the newly attaching PTY. The director writes wake keys and then issues an untargeted `go-to-tab-by-id` (`zellij.py:465–470`). As the backend itself documents (`:142–145`), the action targets the client that most recently received input.

An existing client A can type between our wake and the action. The action then switches A to our target tab. A's next keystroke can reach that tab before scry notices. Killing our new client cannot undo that. The attempted restoration is another untargeted action (`:495–498`): it can move a third client, or undo an intentional switch by A.

This matters directly to the intended environment: existing long-lived SSH clients remain attached, and many scry instances are in use. More retries or a longer confirmation budget do not make these side effects safe. The current live attach test establishes other clients' positions before the operation but does not keep them typing through it (`tests/test_zellij_live.py:170–195`).

**Required change:** require steering that targets the owned client, or an equivalent mechanism whose effects are confined to that client. Do not treat “restore anyone we displaced” as a safety measure. Requiring explicit wake keys is useful configuration hygiene, but does not solve routing races or prove that the keys are harmless in every starting mode. If reliable steering is unavailable, that is an architectural limitation to resolve with the user before implementation.

**3. Blocker — The session-generation identifier is an unresolved dependency, and the suggested source is unsuitable.**

Design lines 161–165 leave the session identity to the builder. Almost every stale-cache guarantee depends on it. Zellij 0.45.1's `list-sessions` does not expose an immutable generation token: `get_sessions()` derives elapsed age from socket creation/modification time, truncates it to whole seconds, and `print_sessions()` formats it as `[Created … ago]`. The displayed value changes while a session remains alive. Reconstructing a timestamp by subtracting that age introduces rounding and observation-time uncertainty. See the pinned upstream [session enumeration and formatting implementation](https://github.com/zellij-org/zellij/blob/v0.45.1/zellij-utils/src/sessions.rs#L17-L43) and [output formatting](https://github.com/zellij-org/zellij/blob/v0.45.1/zellij-utils/src/sessions.rs#L203-L251).

**Required change:** choose and demonstrate the generation mechanism before building the cache/protocol. Specify its namespace, supported platforms, and behavior across restart, resurrection, workspace reboot, and changed `session_group`. A server-lifetime nonce exposed by the server would be ideal. A Linux process/socket-derived identity may be workable, but needs a documented binding and lifecycle argument; a persisted UUID keyed only by session name is insufficient when sessions can restart outside scry.

Test repeated observations of one live session, two rapid restarts with the same name and tab names, and workspace reboot with a persistent home directory. If the identity is unavailable or ambiguous, fail closed. Do not substitute a human-readable age.

**4. Blocker — Preflight checks do not bind subsequent actions to the verified session.**

Design lines 167–177 check identity/name/ID, then attach or rename by session name. The session can exit and restart between these operations. A new session can reuse the tab ID and name; the existing director confirms only a numeric tab ID. Remote listing also needs consistency: reading an old generation followed by a new tab list must not produce one apparently valid response.

The “never resurrect” rule has a separate race. Checking that the session is running before spawning `zellij attach` does not ensure it is still running when Zellij processes the attach. Upstream explicitly takes the resurrection path when the named session is absent and a saved layout exists; this does not require `--create`. See [Zellij 0.45.1 attach selection](https://github.com/zellij-org/zellij/blob/v0.45.1/src/commands.rs#L719-L755). A later identity check may detect the mistake, but cannot undo the forbidden resurrection.

Rename is especially problematic: a restart between verification and `rename-tab-by-id` can mutate a different tab. A post-rename check cannot undo that safely.

**Required change:** define how the operation remains bound to the verified server generation. Rechecking identity/name at input release and bracketing list snapshots with identity checks are useful mitigations, but are not atomic guarantees. A lock among remote scry processes does not constrain session restarts or changes made directly in Zellij. Investigate an existing-session-only attach and generation-bound action mechanism; otherwise narrow the promises explicitly. Cut remote rename from v1 if its stronger mutation guarantee cannot be established.

Also define the guarantee's boundary. A successful initial attach does not ensure that the tab remains open or focused forever. The design should promise safe initial selection under stated concurrency assumptions, rather than imply that one preflight check protects every future keystroke.

**5. High — Resolve commands against the exact snapshot that was displayed.**

The shared cache can change while an instance waits at its prompt. Suppose A displays `b0 = logs`; B refreshes after tabs are reordered, making `b0 = build`. If A rereads the cache while interpreting `b0`, it sends build's current ID, name, and generation. Every remote verification can succeed, although the user selected the displayed logs row. The same issue affects rename.

The design specifies cache reads on redraw but never states this selection invariant. The current local loop already follows the correct pattern: it captures windows, displays them, and passes the same list to `process_command()` (`src/scry/scry.py:467–492`).

**Required change:** capture an immutable display snapshot containing each row's endpoint, generation, raw name, and tab ID. Both attach and rename must resolve indices from that snapshot. Concurrent cache changes affect only the next redraw. Add a two-instance test that rewrites the cache between rendering and command handling.

**6. High — “Overall deadline” must cancel blocking work, not just bound polling.**

Removing the timed gate release is necessary, but the current machinery cannot enforce the proposed deadline. `ZellijCmd` uses `subprocess.run()` without a timeout (`zellij.py:94–102`). Polling checks its deadline only after those commands return (`:204–207`, `:492–499`), and attach performs an unbounded `director.join()` (`:427`). A hung Zellij command can therefore leave the terminal blank and in raw mode indefinitely even with a nominal confirmation budget.

The proxy also accumulates unbounded held input/output and flushes held output on EOF regardless of confirmation (`src/scry/backends/pty_client.py:288–321`). Strict failure needs a deliberate abort state; simply deleting timed release is incomplete.

**Required change:** propagate a monotonic startup deadline into every probe/action subprocess, provide cancellation, bound buffer growth and cleanup, and prevent a late director success from reopening an aborted gate. Discard pending user input on failure and restore the terminal in every exit path. Define cancellation while raw mode is active. Give attach setup and rename bounded waits too; the duration of an established interactive attach must remain unrestricted.

For SSH refresh, enforce a wall-clock deadline covering the whole operation, including the proxy process and cleanup. A connection timeout alone does not bound a stalled remote command. Test a command that never returns, child exit before confirmation, output flooding, cancellation, and a success result arriving after timeout.

**7. Medium — Atomic cache replacement does not prevent lost updates.**

Design lines 143–146 explicitly claim that lockless read/merge/write prevents writers from dropping each other's hosts. It does not. A rename writer can read the file; another process refreshes hosts b and c and replaces it; the rename writer then updates b in its old snapshot and replaces the file, reverting c. Every individual replacement is atomic.

**Required change:** lock the short reread/merge/replace transaction, keeping network requests outside the lock, or use a storage arrangement with independent host records and suitable concurrency semantics. If using a lock file with atomic replacement, lock a stable separate file rather than the inode being replaced. Document and test the chosen behavior on the supported NFS configuration.

Also decide what happens when an older observation completes after a newer one. A slow refresh can overwrite a successful rename with the old name even after write transactions are serialized. Last-writer-wins is an acceptable deliberate display-freshness tradeoff, but it should not be described as preserving the newest observation.

**8. Medium — Display letters are not durable endpoint identities.**

The cache and history must distinguish the configured endpoint from its shortcut letter. Configuration currently loads once per process (`scry.py:89–95,189`). If `b` is changed from one host or executable to another, old and newly launched instances can write different remote data under the same key. A good session-generation token may reject the eventual attach, but the instances can still continually overwrite each other's usable cache.

**Required change:** namespace cache records and history by an endpoint/configuration identity, with the shortcut used only for presentation. Include the actual remote session name in the protocol's session identity. Reject cache records from a different configuration; do not depend on the user restarting every scry process simultaneously. This also avoids assuming that a generation token chosen for uniqueness within one host is globally unique.

**9. High — Parallel SSH refresh needs an explicit noninteractive contract.**

Omitting `-t` does not make SSH authentication noninteractive. SSH can request passwords, key passphrases, or host-key confirmation on the local terminal. Parallel refresh workers can compete for those prompts while scry owns the UI. User SSH configuration can also request a PTY, which undermines the JSON stdout/stderr boundary. See OpenSSH's [BatchMode](https://man.openbsd.org/ssh_config.5#BatchMode), [RequestTTY](https://man.openbsd.org/ssh_config.5#RequestTTY), and [authentication prompt behavior](https://man.openbsd.org/ssh.1#SSH_ASKPASS).

**Required change:** make refresh use explicit `-T`, batch authentication, closed stdin, and separately captured stdout/stderr. Report which host needs an ordinary interactive connection to establish trust/authenticate. Never bypass host-key checks. Account separately for any Coder ProxyCommand interaction; SSH's batch option is not a universal guarantee about an arbitrary child program. Interactive attach can retain its normal authentication behavior.

Specify malformed/extra stdout as a protocol failure, with a useful diagnostic, rather than trying to extract a convenient JSON substring. Define a maximum response size. These requirements should be exercised through a real SSH subprocess, since patched `subprocess` tests will not reveal prompt and PTY behavior.

**10. Medium — The documented remote executable path conflicts with the quoting rule, and Zellij has its own PATH dependency.**

The example `scry: ~/.local/bin/scry` conflicts with blanket `shlex.quote`: quoting it produces `'~/.local/bin/scry'`, preventing remote tilde expansion. Expanding it locally is also wrong when local and remote homes differ.

**Required change:** define a path policy: use an absolute remote path, or explicitly support `~/` by expanding only that prefix on the remote side while safely quoting the suffix. Keep command arguments separate from executable-path handling. Pass option-looking names unambiguously, for example a single quoted `--expect-name=<value>` argument; shell quoting alone does not stop an argument parser interpreting `--help` as an option. Validate the SSH destination as a destination, not an arbitrary option string.

Finding scry is only half of setup. `ZellijBackend.__init__()` looks up `zellij` in PATH (`zellij.py:155`), and the finder has no configured-path fallback (`src/scry/bin_utils.py:23–29`). A full path to scry does not help when Zellij is installed in `~/.cargo/bin` or another profile-only directory. Test the actual noninteractive `remote-list` command in the checklist and provide an explicit remedy for both binaries.

**11. Medium — Rename needs coherent cache/history and uncertain-outcome semantics.**

Updating a tab's cached name after a successful rename immediately makes its old history record fail the design's name-equality cleanup. Attaching to a tab, renaming it, and pressing Enter can therefore lose that tab from history despite it still existing.

A cached-name patch also must not imply that the host's entire tab list, ordering, and client counts were refreshed. Those fields may be days old. Updating the host's “last successful refresh” timestamp would misrepresent them. Patching an entry after another writer has installed a different generation must be conditional on the original target identity.

**Required change:** update matching history entries in the initiating process from the verified rename result; retain explicit invalidation for unrelated external renames. Distinguish “this name was confirmed” from “this host snapshot was refreshed,” or return a complete verified snapshot from the same rename operation. If SSH disconnects after the rename but before confirmation reaches the caller, report “outcome unknown; press z,” rather than claiming the mutation failed or retrying automatically. This is another reason rename is a sensible v1 cut.

**12. Medium — Remote display text must not be interpreted as Rich markup or terminal control.**

The design asks to reuse local formatting. That formatter concatenates raw names into Rich markup (`scry.py:649–672`) and prints the result with markup enabled (`:579`). The delegated review reproduced `MarkupError` for a name such as `[/]logs`. Newlines and control characters can also disrupt the index. A legitimate remote tab name can consequently break every redraw that reads its cached value.

**Required change:** render names, labels, and diagnostic text as literal text, with display-only control-character handling. Keep the unmodified raw name for verification and transport. Test brackets, newlines, escape characters, Unicode, and leading dashes across the complete list/display/attach/rename path. Shell escaping and display escaping solve different problems.

**Smaller scope and integration points to settle in the revised design**

- The existing `list_windows()` discards position and sorts by name (`zellij.py:274–282`). Remote tab-order indexing needs a position-preserving listing operation; do not accidentally change local ordering while reusing the backend.
- Separate headless remote dispatch from interactive startup. The import currently parses CLI arguments and constructs a backend, and `run_scry()` unconditionally enters `do_table_loop()`. Explicitly test that remote subcommands never enter the UI, instantiate an inappropriate backend before protocol validation, or reach session creation.
- Restore the saved local termios state rather than applying a generic “sane” preset. Specify the terminal escape modes reset on success, failure, cancellation, and connection loss; test them through an actual PTY. Include focus reporting and Kitty keyboard mode, both handled by [Zellij's client setup/teardown](https://github.com/zellij-org/zellij/blob/v0.45.1/zellij-client/src/lib.rs), beyond the modes already named in the design.
- The future piggyback-refresh frame would share a byte stream with arbitrary pane output. An escape sequence “terminals ignore” is not an authenticated control channel. A pane can emit the same frame. Keep this out of v1, and do not constrain v1 around it until framing, provenance, size limits, and interruption behavior are designed.
- Clarify whether the unattended-connection restriction permits the explicitly recommended `ControlPersist 10m`. It leaves an SSH master alive after the user action ends. That may be entirely acceptable, but it should be an intentional distinction between “no automatic scry actions” and “no background SSH connection.”

**Suggested implementation gate**

First produce a small demonstration of generation identity, exact owned-client identification, safe client-specific steering, and behavior when the target session exits during attach. Include a concurrently typing pre-existing client and simultaneous manual/scry attaches. Observe which pane receives sentinel input; checking only the director's return value would reproduce the same false-confidence problem as the implementation.

Then implement strict attach with an explicit success/failure state, bounded subprocesses, bounded buffers, and reliable terminal restoration. Add restart-between-checks and no-resurrection tests. If the underlying Zellij interface cannot meet those requirements, revise the design at that point rather than shipping the stated guarantees with a heuristic implementation.

Once that foundation is demonstrated, cache-only rendering, snapshot-bound selections, and explicit parallel refresh are straightforward follow-on work. Rename can wait. Keep tests for concurrent cache writers, cache changes while a prompt is open, changed endpoint configuration, awkward names, SSH authentication/PTY overrides, and connection loss. The proposed tests are a useful start, but faking Zellij identity and confirmation as correct would leave the most consequential assumptions untested.

Validation performed for this review: source and test inspection, installed CLI/version/help inspection, upstream source/documentation checks, the deterministic client-confirmation reproduction described above, and delegated rendering/quoting checks. I did not run live SSH integration tests or mutate any running Zellij session.
