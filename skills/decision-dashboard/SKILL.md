---
name: decision-dashboard
description: Present multiple decisions to Jake as an interactive TUI dashboard instead of walls of terminal text or the native question picker. Use when a task reaches a point requiring 2+ user decisions, or one decision with dense comparative context (tables, tradeoffs, evidence, tickets). The primary path is the MCP tool mcp__decision-dashboard__present_decisions, which shows a tmux popup and returns structured answers as a real tool result.
user_invocable: true
trigger: when a task reaches a multi-decision checkpoint, when Jake asks for a decision dashboard, or when dense comparative context (tickets, tradeoff tables, batch dispositions) needs structured answers
---

# Decision Dashboard

When you need Jake to make multiple decisions — or one decision with dense context — do not dump them as prose in the conversation, and do not use the native AskUserQuestion picker. Present them as an interactive dashboard and consume his structured answers.

## Primary path: the `present_decisions` MCP tool

Call **`mcp__decision-dashboard__present_decisions(title, decisions)`**. It renders the dashboard as a tmux popup over Jake's session, blocks while he answers, and **returns the answers as a real tool result** — you receive structured data directly, then write your response after seeing it. This is the whole mechanism: no manual launching, no polling, no native picker.

- `decisions` is a list of decision objects using the spec schema below (each needs at least `id`, `question`, and — for single/multi — `options`). Put the real substance in each decision's `context_md`.
- The tool returns `{"status": "submitted"|"cancelled"|"unavailable", "answers": {...}}` (answers format below). On `"unavailable"` (no tmux) fall back to AskUserQuestion; on `"cancelled"` apply the cancel guidance below.
- A `PreToolUse` hook auto-redirects **every** AskUserQuestion call here — Jake never wants the native picker. Even a single yes/no goes to the dashboard. The only fall-throughs to native are hard fallbacks where the dashboard cannot run: no tmux, no questions, or unparseable hook input. So just author the `decisions` directly; if you reach for AskUserQuestion out of habit, the hook converts it.

The rest of this skill is the spec/answers schema (which the tool uses) plus a manual fallback for when the MCP tool is unavailable.

## Manual fallback (MCP tool unavailable)

Only when `mcp__decision-dashboard__present_decisions` is not registered. `decision-tui` is on PATH at `~/.local/bin/decision-tui` (source `/mnt/general-storage/repos/decision-tui`); `$TMUX` must be set or fall back to AskUserQuestion.

1. **Write the spec.** Create a JSON decision spec (schema below) at a unique temp path, e.g. `/tmp/decisions-<topic>-$$.json`. Put real substance in `context_md` — comparison tables, evidence, file references. The markdown is the dashboard's whole value; the option list is just the input control. When the source records are thin (empty descriptions, no comments, no history), say so explicitly in `context_md` — e.g. "Ticket has no description or comments; only the title is known" — so a sparse panel reads as a fact about the source, not a rendering failure. Never pad thin sources with invented detail.

2. **Validate it** (a malformed spec opens a broken pane in Jake's face):
   ```bash
   decision-tui --validate /tmp/decisions-X.json
   ```

3. **Present it.** ALWAYS use the popup. The popup is a modal overlay that grabs focus and covers the screen — so make the dashboard the entire message of the turn: anything Jake must read goes in `context_md`, not in conversation prose alongside (it would be hidden until he dismisses the dashboard). Your accompanying text is one short "dashboard is open" line and nothing else.

   Always launch via `decision-tui-popup` (NEVER raw `tmux display-popup` — tmux silently discards a popup when the client already shows one: exit 0, command never runs, answers file never appears; the launcher serializes popups per client with flock so concurrent dashboards queue). Background the launch (`&`) — it blocks until the popup closes:
   ```bash
   ANSWERS=/tmp/answers-X.json
   rm -f "$ANSWERS"
   decision-tui-popup /tmp/decisions-X.json "$ANSWERS" >/tmp/popup-launch.log 2>&1 &
   ```
   Use a split pane ONLY when Jake explicitly asks for one:
   ```bash
   tmux split-window -h -l 55% "decision-tui /tmp/decisions-X.json $ANSWERS"
   ```

4. **Tell Jake** in one short line that the dashboard is open (keys: arrows to navigate decisions, `ctrl+s` submit, `esc` cancel).

5. **Poll for the answers file.** Loop with `sleep`, total wait under ~8 minutes per Bash call (harness kills at ~10 min); if he hasn't answered, just poll again in a fresh call — do not give up:
   ```bash
   for i in $(seq 1 90); do [ -f "$ANSWERS" ] && break; sleep 5; done; cat "$ANSWERS" 2>/dev/null || echo "NOT_ANSWERED_YET"
   ```

6. **Act on the answers.** `status` is `submitted` or `cancelled`. On `cancelled`, read the timing: an **immediate cancel** (seconds after opening) usually means "I just wanted to look" or "not now" — treat it as a dismissal, summarize the pending decisions briefly in conversation, and do NOT re-open the dashboard unasked. A **considered cancel** (after real time in the dashboard) more likely means the options don't fit — ask in plain conversation what's wrong with the framing. Clean up the temp files afterwards.

## Concurrency

- **Popups queue.** Multiple dashboards triggered at once (parallel subagents, overlapping skills) appear one at a time per tmux client — `decision-tui-popup`'s flock guarantees it. Each caller polls its own answers file, so order doesn't matter.
- **Split panes coexist** — multiple `split-window` dashboards open side by side; fine for 2, cramped beyond that.
- Every termination path writes an answers file (`submitted`, or `cancelled` on esc/kill/popup teardown — the app traps SIGTERM/SIGHUP), so polling always terminates. Still keep the per-call poll under ~8 minutes and re-poll in a fresh Bash call.
- Answers paths must be unique per dashboard (the `$$`/topic naming above guarantees this); two dashboards sharing one answers file would corrupt the handshake.

## Voice input (text fields)

Any text field — a `text` decision's answer box, the auto-appended "Other…" box, and every "notes" box — accepts **local voice dictation**. Jake focuses the field and presses **`ctrl+r`** to start recording (the border shows `● REC`), presses `ctrl+r` again to stop; the clip is transcribed on-box (faster-whisper `small.en`, CPU, no audio leaves the machine) and inserted at the cursor. The model is pre-warmed when the dashboard opens, so the first dictation has no cold-start stall.

This is automatic — nothing to configure in a spec. It just means `text` decisions and `notes` are cheap for Jake to answer at length, so prefer a `text` decision (or lean on `notes`) when you genuinely want prose, rather than forcing everything into rigid options. Voice degrades gracefully: if `faster-whisper` or a recorder (`pw-record`/`arecord`) is missing, `ctrl+r` just reports voice is unavailable and typing is unaffected.

(Implementation note: true hold-to-talk was investigated and is impossible in the tmux popup — tmux strips the kitty keyboard protocol and emits no key-release events — so the interaction is a press/press toggle, not hold-to-talk. Don't re-attempt kitty release events here.)

## Spec schema

```json
{
  "title": "short dashboard title",
  "context_md": "optional markdown shown on every decision panel",
  "decisions": [
    {
      "id": "unique-slug",
      "question": "The question?",
      "type": "single | multi | text",
      "required": true,
      "context_md": "markdown: tables, tradeoffs, evidence. Make this dense and useful.",
      "options": [
        {"id": "slug", "label": "Short label", "description": "tradeoffs / why", "recommended": false}
      ]
    }
  ]
}
```

- `type` defaults to `single`. `options` is required for `single`/`multi`, ignored for `bool`/`text`.
- `bool` renders a single checkbox row (set its text with `"label": "Ship it"`, default "Yes") and always answers with canonical `"true"`/`"false"` — checked is true, unchecked is false. A checkbox is never unanswered: `required` has no effect and the default outcome is `false`. Use it for yes/no calls; if Jake must be forced to choose explicitly, use a two-option `single` instead. No Other option; nuance arrives in `notes`.
- `required` defaults to `true`; submit is blocked until all required decisions are answered.
- Every `single`/`multi` decision automatically gets an "Other…" free-text option appended by the renderer — NEVER define your own Other/custom/escape-hatch option in a spec.
- Mark at most one option per decision `recommended` — and only when you genuinely recommend it.
- Per Jake's global rules: no time estimates anywhere in the spec; frame options by structural tradeoffs (scope, reversibility, blast radius, what each enables next), never by effort or difficulty.

## Answers format

```json
{
  "status": "submitted",
  "answers": {
    "unique-slug": {"type": "single", "selected": ["slug"], "other": "", "notes": "optional free text Jake typed"}
  }
}
```

- `selected` holds option ids (for `text`: a single-element array with the text, empty if blank).
- `other` holds text Jake typed into the automatic "Other…" option — when non-empty, it IS his answer (for `single`, `selected` will be empty; for `multi`, it adds to any selected options). Treat it with the same weight as a listed option.
- Always read `notes` — Jake uses it to qualify or override an option.

## Example

A working sample spec lives at `/mnt/general-storage/repos/decision-tui/examples/sample-spec.json`.

For the canonical dense use case — batch ticket triage where each decision carries the original ticket, comment thread, work history, and proposed fix — imitate `/mnt/general-storage/repos/decision-tui/examples/ticket-triage.json`: metadata table first, then `### Original ticket` (blockquoted), `### Comments` (blockquoted, attributed, dated), `### Work history` (bullets ending in the agent's confidence/recommendation), `### Proposed fix` (fenced code). Per-ticket disposition decisions first, then batch-level decisions (release bundle as `multi`, flags as `bool`, free-form notes as `text`) that reference the earlier ones.

Demo the simple spec with:

```bash
decision-tui-popup /mnt/general-storage/repos/decision-tui/examples/sample-spec.json /tmp/demo-answers.json &
```
