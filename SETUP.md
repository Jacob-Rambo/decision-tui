# Setup

Decision TUI has four moving parts:

- Claude Code, the agent that asks for decisions.
- Decision TUI, this tool: a Textual app, an MCP server, a hook, and a skill.
- tmux, which lets the agent open an interactive popup from a process that has no TTY.
- A terminal. Any works; Ghostty adds a per-tab bell that shows which tab a dashboard opened in.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- tmux 3.x and Claude Code
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

- tmux: `set -g mouse on`, required for clickable links.
- Ghostty: the default `bell-features` includes `attention`, which drives the
  per-tab indicator. No change needed unless you've overridden it.

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
