# Setup — Ghostty + tmux + Claude Code + decision-tui

This is the full workflow for getting **terminal-native decision dashboards** in
Claude Code: instead of dumping walls of prose or the built-in question picker,
Claude opens an interactive TUI in a tmux popup, you answer, and structured
answers go straight back to the model.

It's four pieces:

| Piece | Role |
|---|---|
| **Claude Code** | the agent that needs your decisions |
| **decision-tui** | this tool — Textual TUI + an MCP server + a hook + a skill |
| **tmux** | the trick that lets a TTY-less agent spawn an interactive popup |
| **Ghostty** *(or any terminal)* | where it all runs; Ghostty adds the per-tab bell that tells you which tab a dashboard opened in |

## Why tmux is required

Claude Code's tools run as subprocesses with **no controlling TTY**, so they
can't draw a full-screen TUI over your session. tmux's server socket is the way
out: a TTY-less process can ask the tmux server to open an interactive
`display-popup`. That popup has a real terminal, renders the dashboard, and
collects your answers. No tmux → no popup (see *Without tmux* at the end).

## Prerequisites

- **Python ≥ 3.12** and [**uv**](https://docs.astral.sh/uv/)
- **tmux** (3.x+) and **Claude Code**
- A terminal — any works; **Ghostty** gives the per-tab bell indicator
- *Optional, for voice dictation:* a mic and one of `pw-record` (PipeWire) or
  `arecord` (ALSA); `ffmpeg` helps. `faster-whisper` installs as a dependency.

## Install

```bash
git clone https://github.com/Jacob-Rambo/decision-tui
cd decision-tui
uv tool install -e .          # puts decision-tui + decision-tui-mcp on PATH

# the popup launcher is a shell script — link it onto your PATH
ln -s "$PWD/bin/decision-tui-popup" ~/.local/bin/decision-tui-popup
```

### 1. Register the MCP server

This exposes the `present_decisions` tool to Claude:

```bash
claude mcp add decision-dashboard --scope user -- decision-tui-mcp
```

Verify: `claude mcp list` should show `decision-dashboard: … ✔ Connected`.

### 2. Install the skill

```bash
bin/sync-skills        # copies skills/decision-dashboard into ~/.claude/skills
```

(Pass extra dirs as arguments if you run more than one Claude config dir, e.g.
`bin/sync-skills ~/.claude-work/skills`.)

### 3. (Recommended) Install the redirect hook

This makes Claude route its native `AskUserQuestion` picker to the dashboard.
Add to `~/.claude/settings.json` (use the absolute path to your clone):

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

The hook denies `AskUserQuestion` instantly and tells the model to call
`present_decisions` instead. It falls through to the native picker only when the
dashboard can't render (no tmux, no questions, unparseable input). Hook *config*
is snapshotted at session start — adding it needs a fresh session — but edits to
the hook *script* are live immediately.

## Config requirements

- **tmux:** `set -g mouse on` — clickable links in the dashboard need mouse
  support. (One tmux server with many clients/tabs is fine; the popup routes to
  the tab running Claude.)
- **Ghostty:** the default `bell-features` already includes `attention`, which
  is the per-tab indicator used to flag which tab a dashboard opened in. No
  change needed unless you've overridden it.

## What you get

- **Multi-decision dashboard** — a sidebar of decisions, each with rich markdown
  context (tables, evidence, diffs). Single / multi / yes-no / free-text answers,
  an auto-appended "Other…" option, and per-decision notes.
- **Clickable links** in context — `http`/`https`/`mailto` open in your browser,
  `file:` links (local screenshots/recordings) in your default viewer; bare URLs
  are autolinked.
- **Voice dictation** — focus any text field and press `ctrl+r` to dictate;
  transcription is fully local (faster-whisper, CPU), nothing leaves your machine.
- **Auto-growing text boxes**; `ctrl+c` clears a field.
- **Multi-tab aware** — the popup opens on the tab running Claude and rings that
  tab's bell so you can find it among several tabs.

## Usage

- **Automatic:** Claude calls `present_decisions` (or the hook redirects a native
  question) whenever it needs your input. Answers return to the model as data.
- **Manual / demo:**
  ```bash
  decision-tui-popup examples/sample-spec.json /tmp/answers.json
  cat /tmp/answers.json
  ```

The spec/answers schema is documented in the `decision-dashboard` skill
(`skills/decision-dashboard/SKILL.md`) and in `decision_tui/spec.py`. Working
examples are in `examples/`.

## Without tmux

The dashboard currently requires tmux to spawn its popup; with no tmux the MCP
tool reports `unavailable` and Claude falls back to its native question picker.
Opening the dashboard in a fresh terminal window instead (so it works tmux-less)
is a planned enhancement.
