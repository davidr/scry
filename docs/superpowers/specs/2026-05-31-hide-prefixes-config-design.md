# Design: `hide_prefixes` config option

**Date:** 2026-05-31
**Status:** Approved

## Problem

`format_window_strings()` in `src/scry/scry.py` collapses shared window-name
prefixes: when a window name matches `prefix+NN` (digits) and the window above
it in the same column shares the same prefix, the prefix is blanked to spaces so
only `+NN` shows. This is always on and can be visually distracting. Users want
to enable or disable it.

## Decisions

- **Config key / flag name:** `hide_prefixes` (config) / `--hide-prefixes` (CLI).
- **Default:** `False` (off). The collapse becomes opt-in; existing users see
  full names after upgrade unless they enable it.
- **CLI flag:** yes, `action="store_true"`, matching the rest of the options.

## Design

### Behavior

Gate the existing prefix-collapse block (`scry.py` lines ~631–642) behind the new
boolean config key. When `hide_prefixes` is false (default), names always render
in full. When true, today's collapsing behavior applies.

### Config plumbing (three-tier: defaults -> ~/.scry.yml -> CLI)

1. Add `"hide_prefixes": False` to `default_config`.
2. Add argparse flag `--hide-prefixes` with `action="store_true"` and help text.
3. Override: `if args.hide_prefixes: cfg["hide_prefixes"] = True` — same pattern
   as the existing `--debug` flag.

### Display change

Line ~631's condition becomes:

```python
if config["hide_prefixes"] and row != 0 and not is_highlighted:
```

### Tradeoff (accepted)

Like `--debug`, a `store_true` flag can only turn the feature *on* from the CLI.
A user who sets `hide_prefixes: true` in `~/.scry.yml` cannot override it back off
via CLI. Accepted for consistency with existing flags; default-off makes the
common case (enable for one run) work. Rejected alternative:
`argparse.BooleanOptionalAction` (`--hide-prefixes` / `--no-hide-prefixes`).

## Testing

Add cases to the `format_window_strings` tests (patching `config["hide_prefixes"]`):

- With `hide_prefixes=True`: a matching `prefix+NN` row below a same-prefix row is
  blanked (prefix replaced with spaces).
- With `hide_prefixes=False`: the full name is preserved.

## Docs

- Update `CLAUDE.md` (config-tier note; refresh test count).
- Update `README.md` (document the option and flag).
