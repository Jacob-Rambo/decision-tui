# decision-tui

Generic terminal decision dashboard. Renders a JSON decision spec as a Textual TUI
(sidebar of decisions, markdown context with tables, single/multi/text inputs),
writes answers as JSON. Built so Claude Code can present dense multi-decision
checkpoints in a tmux split pane instead of walls of terminal text — the agent
generates *data* (the spec), never UI code.

## Usage

```bash
decision-tui <spec.json> <answers.json>   # run the dashboard
decision-tui --validate <spec.json>       # validate a spec, exit 0/1
```

Keys: arrows navigate decisions (ctrl+up/down from anywhere), tab moves focus,
`ctrl+s` submit (blocked until all required decisions answered), `esc` cancel.

Spec and answers schemas are documented in `decision_tui/spec.py`; a working
example is in `examples/sample-spec.json`.

## How Claude uses it

The `decision-dashboard` skill (`~/.claude/skills/decision-dashboard/SKILL.md`):
write spec → `--validate` → present (default `decision-tui-popup spec answers &`
for a modal overlay; `tmux split-window -h -l 55%` to keep the conversation visible)
→ poll for the answers file → act on it. Works because tmux is socket-based, so
Claude's TTY-less Bash subprocesses can spawn panes; falls back to AskUserQuestion
outside tmux.

## Concurrency

tmux **silently discards** `display-popup` when the client already shows a popup
(exit 0, command never runs — verified empirically on tmux 3.6). `bin/decision-tui-popup`
(symlinked into `~/.local/bin`) serializes popups per client with flock, so
concurrent dashboards queue and appear one after another. The app also traps
SIGTERM/SIGHUP and writes a `cancelled` answers file, so every termination path
ends the caller's poll. Split-pane dashboards coexist without coordination.

## Dev

```bash
uv sync
uv run python scripts/smoke_test.py   # headless pilot test of all answer types + cancel
uv tool install -e . --force          # (re)install the CLI shim into ~/.local/bin
```
