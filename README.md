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

The primary path is the **MCP server** (`decision_tui/mcp_server.py`, entry point
`decision-tui-mcp`): one tool, `present_decisions(title, decisions)`, which runs
the popup and returns the answers as a real MCP tool result. The model receives
structured answers as data — no native question UI, no deny-reason
reinterpretation, no polling. A `PreToolUse` hook
(`bin/askuserquestion-dashboard-hook`) redirects dense native AskUserQuestion
calls to this tool (instant deny — never blocks).

The `decision-dashboard` skill documents the spec/answers schema and a manual
fallback (`decision-tui-popup spec answers &` → poll the answers file) for when
the MCP tool is unavailable.

### Registering the MCP server (both Claude instances)

The registration lives in each instance's `.claude.json` (a large state file we
don't version) — reproduce it with:

```bash
claude mcp add decision-dashboard --scope user -- decision-tui-mcp
CLAUDE_CONFIG_DIR="$HOME/.claude-work" claude mcp add decision-dashboard --scope user -- decision-tui-mcp
```

Verify with `claude mcp list` (expect `decision-dashboard: … ✔ Connected`). The
PreToolUse hook is wired in `settings.json` (tracked in personal-configs).

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
