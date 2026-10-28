"""MCP server exposing the decision dashboard as a real tool.

One tool, ``present_decisions``: render a decision spec in the tmux popup,
block while Jake answers, and return the answers as a structured tool result.
Because this is a genuine MCP tool result (not a PreToolUse deny-reason), the
model receives the answers as data it consumes directly — there is no native
question UI involved at all when the model calls this tool.

Registered with both Claude instances as the server `decision-dashboard`. The
companion PreToolUse hook redirects any native AskUserQuestion call here.

Answer durability
-----------------
Answers a human typed are expensive, so they are never written to a temp path
and never deleted by this module. Every dashboard run writes its answers to::

    $XDG_STATE_HOME/decision-tui/answers/   (default ~/.local/state/...)

as ``answers-<UTC timestamp>-<rand>-<title slug>.json``. The timestamp is
lexicographically sortable, so the newest run is simply the last entry::

    ls -1 ~/.local/state/decision-tui/answers/answers-*.json | tail -1

This matters because the MCP caller can go away while the popup is still open
(Claude Code aborts a tool call on its idle timeout). When that happens the
popup still collects the answers and this module still writes them, but the
tool result goes nowhere. The file on disk is the only surviving copy, so it
stays. Only the throwaway *spec* file is cleaned up.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from decision_tui.spec import SpecError, validate_spec

mcp = FastMCP("decision-dashboard")

LAUNCHER = shutil.which("decision-tui-popup") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "decision-tui-popup"
)

#: Filename prefix for persisted answer payloads. Kept as a constant so
#: recovery tooling and the glob in :func:`latest_answers_path` agree.
ANSWERS_PREFIX = "answers-"


def answers_dir() -> Path:
    """Return the durable directory that persisted answer payloads live in.

    Follows the XDG Base Directory spec for *state* data: answers are neither
    config nor cache, they are state a human produced that should survive a
    reboot but is not worth backing up. Honors ``XDG_STATE_HOME`` when set and
    falls back to ``~/.local/state``.

    Returns:
        Path to ``<state home>/decision-tui/answers``. The directory is not
        created here; see :func:`new_answers_path`.
    """
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "decision-tui" / "answers"


def _slug(title: str, limit: int = 48) -> str:
    """Reduce a dashboard title to a short filename-safe slug.

    Purely cosmetic: it makes a directory listing readable so a human scanning
    for "that ticket triage dashboard" can find it without opening each file.
    Never affects sort order, which is carried entirely by the timestamp.

    Args:
        title: The dashboard title as the model supplied it.
        limit: Maximum slug length in characters.

    Returns:
        Lowercase ``[a-z0-9-]`` slug, or ``"decisions"`` when nothing survives
        normalization (for example a title that is entirely punctuation).
    """
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:limit].strip("-") or "decisions"


def new_answers_path(title: str) -> Path:
    """Allocate a durable, sortable path for one dashboard run's answers.

    Creates the answers directory if it is missing. The filename is
    ``answers-<YYYYMMDDTHHMMSSZ>-<6 hex>-<slug>.json``: the UTC timestamp makes
    plain lexicographic sort match chronological order, the random suffix keeps
    two dashboards opened in the same second from colliding, and the slug makes
    the file self-describing.

    Args:
        title: The dashboard title, used only for the readable slug.

    Returns:
        Path that does not yet exist, inside :func:`answers_dir`.
    """
    d = answers_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return d / f"{ANSWERS_PREFIX}{stamp}-{uuid.uuid4().hex[:6]}-{_slug(title)}.json"


def latest_answers_path() -> Path | None:
    """Return the most recent persisted answers file, or None if there are none.

    Recovery entry point for a later session (or a human) that needs to find
    answers a timed-out caller never received. Sorting is by filename, which is
    equivalent to sorting by time because of the leading UTC timestamp, and is
    therefore immune to mtime being rewritten by a backup or a copy.

    Returns:
        Path to the newest ``answers-*.json``, or None when the directory is
        absent or empty.
    """
    d = answers_dir()
    if not d.is_dir():
        return None
    files = sorted(d.glob(f"{ANSWERS_PREFIX}*.json"))
    return files[-1] if files else None


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

    Note:
        The same payload is also written to
        ``$XDG_STATE_HOME/decision-tui/answers/`` (default
        ``~/.local/state/decision-tui/answers/``) and is never deleted by this
        server. If your call was aborted by a client-side timeout while the
        popup was open, the answers are still there: take the newest
        ``answers-*.json`` (filenames sort chronologically).
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

    # Durable, outside /tmp, and deliberately not covered by the finally below.
    answers_path = new_answers_path(title)

    try:
        proc = subprocess.run([LAUNCHER, str(spec_path), str(answers_path)], capture_output=True, text=True)
        if proc.returncode != 0:
            return json.dumps(
                {"status": "unavailable", "answers": {}, "error": proc.stderr.strip()[:300]}
            )
        try:
            payload = answers_path.read_text()
        except OSError as e:
            # The popup exited clean but left nothing readable. Report rather
            # than raise, so the model can fall back instead of seeing a crash.
            return json.dumps(
                {"status": "unavailable", "answers": {}, "error": f"answers unreadable: {e}"}
            )
        return payload
    finally:
        # Only the spec is disposable. Deleting answers is never this block's
        # job: when the caller has already timed out, the file on disk is the
        # sole surviving record of what Jake typed.
        #
        # No pruning policy is applied on purpose. These payloads are a few KB
        # each and are produced a handful of times a week, so unbounded growth
        # is measured in megabytes per decade. Any automatic delete would risk
        # destroying answers no caller ever received, which is exactly the
        # failure this design exists to prevent. Clear the directory by hand if
        # it ever matters.
        try:
            os.unlink(spec_path)
        except OSError:
            pass


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
