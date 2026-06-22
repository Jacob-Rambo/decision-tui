# Setup

Decision TUI has four moving parts:

- Claude Code, the agent that asks for decisions.
- Decision TUI, this tool: a Textual app, an MCP server, a hook, and a skill.
- tmux, which lets the agent open an interactive popup from a process that has no TTY.
- A terminal. Any works; Ghostty adds a per-tab bell that shows which tab a dashboard opened in.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- tmux 3.2+ (the popup uses `display-popup`, added in 3.2) and Claude Code
- For voice dictation: a mic and `pw-record` (PipeWire) or `arecord` (ALSA); `ffmpeg` helps. faster-whisper installs as a dependency.

## Install

```bash
git clone https://github.com/Jacob-Rambo/decision-tui
cd decision-tui
uv tool install -e .          # installs decision-tui and decision-tui-mcp
ln -s "$PWD/bin/decision-tui-popup" ~/.local/bin/decision-tui-popup
```

### Register the MCP server

```bash
claude mcp add decision-dashboard --scope user -- decision-tui-mcp
```

`claude mcp list` should show decision-dashboard connected.

### Install the skill

```bash
bin/sync-skills
```

This copies `skills/decision-dashboard` into `~/.claude/skills`. Pass extra
directories as arguments if you run more than one Claude config dir, for example
`bin/sync-skills ~/.claude-work/skills`.

### Install the hook (recommended)

This routes Claude's AskUserQuestion picker to the dashboard. Add to
`~/.claude/settings.json`, using the absolute path to your clone:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "AskUserQuestion",
        "hooks": [
          {
            "type": "command",
            "command": "/abs/path/to/decision-tui/bin/askuserquestion-dashboard-hook",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The hook denies AskUserQuestion and tells the model to call present_decisions
instead. It falls back to the native picker only when the dashboard can't render:
no tmux, no questions, or unparseable input. Adding the hook needs a fresh
session; edits to the hook script take effect immediately.

## Configuration

The tool needs almost no tmux config — the launcher sets popup size, border, and
target client itself. The one thing it can't set for you is mouse mode, which
clickable links and popup interaction depend on. Add to `~/.tmux.conf`:

```tmux
# Required: enables clickable links and mouse interaction inside the popup.
set -g mouse on
```

Reload with `tmux source-file ~/.tmux.conf` (or start a fresh session). Nothing
else is required: the popup's geometry (`-w 92% -h 90%`), rounded border, and
per-tab routing are all flags in `bin/decision-tui-popup`, not user settings.

- Ghostty: the default `bell-features` includes `attention`, which drives the
  per-tab indicator. No change needed unless you've overridden it.

### Reference setup (optional)

None of this is required — Decision TUI runs in any terminal with tmux. But if
you want a known-good environment to crib from, here's the full stack the tool
was built and tested against: **Ghostty + tmux + Starship**. The pieces that
specifically help Decision TUI are flagged inline.

**Ghostty** (`~/.config/ghostty/config`):

```ini
# Theme — Decision TUI inherits the terminal/tmux palette rather than imposing
# its own, so a truecolor theme like Dracula carries straight into the dashboard.
theme = Dracula

font-family = DejaVu Sans Mono
font-size = 13

window-padding-x = 8
window-padding-y = 8
window-padding-balance = true
confirm-close-surface = false

cursor-style = bar
cursor-style-blink = false

mouse-hide-while-typing = true
copy-on-select = clipboard

scrollback-limit = 10000
shell-integration = detect
bold-is-bright = true

# Auto-launch tmux so the popup always has a tmux server to open into.
# (bell-features defaults to attention,title — that's what flags the tab a
# dashboard opened in. Don't override it away.)
command = /home/jake/.config/ghostty/tmux-session.sh
```

The `command` line points at a small helper that attaches to an existing tmux
session or starts one, so every Ghostty window lands inside tmux automatically
(adjust the path to your clone):

```bash
#!/bin/bash
# Attach to an unattached tmux session, or start the server if none is running.
if tmux list-sessions &>/dev/null; then
    unattached=$(tmux list-sessions -F '#{session_name} #{session_attached}' \
        | awk '$2 == "0" {print $1; exit}')
    [ -n "$unattached" ] && exec tmux attach-session -t "$unattached" || exec tmux new-session
fi
exec tmux new-session -s default
```

**Starship** — default config is fine; just initialize it in your shell rc
(`~/.bashrc`):

```bash
eval "$(starship init bash)"
```

**tmux** (`~/.tmux.conf`) — the lines that matter for Decision TUI are marked
`# ← Decision TUI`; the rest is general comfort:

```tmux
# Prefix on Ctrl+a
unbind C-b
set -g prefix C-a
bind C-a send-prefix

# Truecolor — so the dashboard's inherited theme renders correctly  # ← Decision TUI
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"

# Mouse — required for clickable links + popup interaction            # ← Decision TUI
set -g mouse on

# Focus events — lets the popup and TUI track focus cleanly           # ← Decision TUI
set -g focus-events on

set -g base-index 1
setw -g pane-base-index 1
set -g history-limit 50000
set -sg escape-time 10
set -g xterm-keys on
set -g renumber-windows on

# Status bar
set -g status-position top
set -g status-style "bg=default,fg=white"
set -g status-left "#[bold]#S  "
set -g status-right "#{pane_current_path}  %H:%M"

# Session persistence across restarts (tpm + resurrect + continuum)
set -g @plugin 'tmux-plugins/tpm'
set -g @plugin 'tmux-plugins/tmux-resurrect'
set -g @plugin 'tmux-plugins/tmux-continuum'
set -g @continuum-restore 'on'
run '~/.tmux/plugins/tpm/tpm'   # keep at bottom
```

The persistence plugins (resurrect/continuum) pair well with the auto-launch
helper: a fresh Ghostty window restores your prior tmux session, so the agent
always has a live, mouse-enabled tmux to pop the dashboard into.

## Usage

Claude calls present_decisions (or the hook redirects a question) when it needs
input. To run it yourself:

```bash
decision-tui-popup examples/sample-spec.json /tmp/answers.json
cat /tmp/answers.json
```

The spec and answers schema is documented in the decision-dashboard skill
(`skills/decision-dashboard/SKILL.md`) and in `decision_tui/spec.py`. More
examples are in `examples/`.

## Without tmux

The popup needs tmux. Without it the MCP tool returns unavailable and Claude uses
its question picker. A fallback that opens a separate terminal window is planned.
