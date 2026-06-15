"""MCP server exposing the decision dashboard as a real tool.

One tool, ``present_decisions``: render a decision spec in the tmux popup,
block while Jake answers, and return the answers as a structured tool result.
Because this is a genuine MCP tool result (not a PreToolUse deny-reason), the
model receives the answers as data it consumes directly — there is no native
question UI involved at all when the model calls this tool.

Registered with both Claude instances as the server `decision-dashboard`. The
companion PreToolUse hook redirects any native AskUserQuestion call here.
"""

import json
import os
import shutil
import subprocess
import tempfile

from mcp.server.fastmcp import FastMCP

from decision_tui.spec import SpecError, validate_spec

mcp = FastMCP("decision-dashboard")

LAUNCHER = shutil.which("decision-tui-popup") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "decision-tui-popup"
)


@mcp.tool()
def present_decisions(title: str, decisions: list[dict]) -> str:
    """Show Jake an interactive decision dashboard and return his answers.

    Use this instead of AskUserQuestion whenever you need 2+ decisions, or one
    decision with dense comparative context (tickets, tradeoff tables, diffs,
    evidence). It renders a tmux popup over Jake's session and blocks until he
    submits or cancels.

    Args:
        title: Short dashboard title (e.g. "Ticket triage — 5 items").
        decisions: List of decision objects. Each has:
            - id: unique slug (required)
            - question: the question text (required)
            - type: "single" | "multi" | "bool" | "text" (default "single")
            - context_md: markdown shown on the panel — put the real substance
              here (tables, ticket bodies, diffs, evidence). When source data is
              thin, say so explicitly rather than padding with invented detail.
            - options: for single/multi — list of {id?, label, description?,
              recommended?}. Do NOT add an "Other" option; the dashboard appends
              a free-text one automatically.
            - label: for bool — the checkbox row text (default "Yes").
            - required: default true (no effect on bool).

    Returns:
        JSON string: {"status": "submitted"|"cancelled"|"unavailable",
        "answers": {<id>: {"type", "selected": [...], "other": "", "notes": ""}}}.
        On "submitted", selected holds option ids (canonical "true"/"false" for
        bool; the typed text for "text"); "other" holds free-text from the
        auto-appended Other option; always read "notes". On "unavailable" the
        dashboard could not be shown (no tmux) — fall back to AskUserQuestion.
    """
    spec = {"title": title, "decisions": decisions}
    try:
        validate_spec(spec)
    except SpecError as e:
        # surface validation errors to the model so it can fix the spec
        raise ValueError(f"invalid decision spec: {e}") from e

    if not os.environ.get("TMUX"):
        return json.dumps({"status": "unavailable", "answers": {}})

    fd, spec_path = tempfile.mkstemp(prefix="mcp-spec-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(spec, f)
    answers_path = spec_path.replace("mcp-spec-", "mcp-answers-")

    try:
        proc = subprocess.run([LAUNCHER, spec_path, answers_path], capture_output=True, text=True)
        if proc.returncode != 0:
            return json.dumps(
                {"status": "unavailable", "answers": {}, "error": proc.stderr.strip()[:300]}
            )
        with open(answers_path) as f:
            return f.read()
    finally:
        for p in (spec_path, answers_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
