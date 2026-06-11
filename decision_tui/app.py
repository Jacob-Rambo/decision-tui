"""decision-tui: render a JSON decision spec as a Textual dashboard, write answers as JSON.

Usage:
    decision-tui <spec.json> <answers.json>
    decision-tui --validate <spec.json>
"""

import signal
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    RadioButton,
    RadioSet,
    SelectionList,
    TextArea,
)
from textual.widgets.selection_list import Selection

from decision_tui.spec import SpecError, load_spec, option_id, write_answers

ANSWERED = "[green]●[/green]"
UNANSWERED = "[dim]○[/dim]"

# value of the auto-appended free-text option on every single/multi decision
OTHER = "__other__"


def _option_label(option: dict) -> str:
    return option["label"] + (" ★" if option.get("recommended") else "")


class DecisionRadio(RadioButton):
    """Radio button drawn as (●) instead of the block-glyph ▐●▌ default."""

    BUTTON_LEFT = "("
    BUTTON_RIGHT = ")"


def _decision_md(spec: dict, decision: dict) -> str:
    parts = [f"## {decision['question']}"]
    if spec.get("context_md"):
        parts.append(spec["context_md"])
    if decision.get("context_md"):
        parts.append(decision["context_md"])
    options = decision.get("options") or []
    described = [o for o in options if o.get("description")]
    if described:
        lines = ["### Options"]
        for o in options:
            rec = " — **recommended ★**" if o.get("recommended") else ""
            desc = f": {o['description']}" if o.get("description") else ""
            lines.append(f"- **{o['label']}**{rec}{desc}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


class DecisionApp(App):
    TITLE = "Decision Dashboard"

    # Render with the terminal's own ANSI palette so the dashboard inherits
    # the terminal/tmux theme instead of imposing Textual's.
    ansi_color = True

    # Styling note: in ANSI mode (ansi_color=True) alpha blends are impossible,
    # so Textual defaults built on them degrade — markdown table keylines
    # ($foreground 20%) vanish and Input backgrounds ($boost) render as odd
    # blocks. Everything here uses explicit ansi_* colors: ansi_default is the
    # terminal's own fg/bg, so the theme still shows through.
    CSS = """
    #nav {
        width: 34;
        border-right: tall ansi_bright_black;
        padding: 1 0;
    }
    #nav:focus-within {
        border-right: tall ansi_blue;
    }
    #nav ListItem {
        padding: 0 1;
        margin: 0 1 1 0;
    }
    ContentSwitcher {
        width: 1fr;
    }
    .panel {
        padding: 0 2 1 2;
    }
    RadioSet, SelectionList {
        margin: 1 0;
        width: 100%;
        border: round ansi_bright_black;
        padding: 0 1;
        background: ansi_default;
    }
    RadioSet:focus-within, SelectionList:focus {
        border: round ansi_blue;
        background-tint: transparent;
    }
    RadioSet {
        & > RadioButton > .toggle--button {
            background: ansi_default;
            color: ansi_bright_black;
        }
        & > RadioButton.-on > .toggle--button {
            color: ansi_green;
            text-style: bold;
        }
        & > RadioButton.recommended > .toggle--label {
            color: ansi_yellow;
        }
        &:focus > RadioButton.-selected > .toggle--label {
            background: ansi_default;
            color: ansi_default;
            text-style: bold reverse;
        }
        &:blur > RadioButton.-selected > .toggle--label {
            background: ansi_default;
            text-style: underline;
        }
    }
    SelectionList {
        & > .selection-list--button {
            background: ansi_default;
            color: ansi_bright_black;
        }
        & > .selection-list--button-selected {
            background: ansi_default;
            color: ansi_green;
        }
        & > .selection-list--button-highlighted {
            background: ansi_default;
        }
        & > .selection-list--button-selected-highlighted {
            background: ansi_default;
            color: ansi_green;
        }
    }
    TextArea.answer {
        height: 8;
        margin: 1 0;
        border: round ansi_bright_black;
        background: ansi_default;
    }
    TextArea.answer:focus {
        border: round ansi_blue;
    }
    Input {
        background: ansi_default;
        border: tall ansi_bright_black;
    }
    Input:focus {
        border: tall ansi_blue;
    }
    Input.other {
        margin: 0 0 1 0;
    }
    Input.notes {
        margin: 1 0 0 0;
    }
    MarkdownTableContent {
        keyline: thin ansi_bright_black;
    }
    MarkdownTableContent > .header {
        color: ansi_blue;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "submit", "Submit all"),
        Binding("escape", "cancel", "Back / Cancel"),
        Binding("ctrl+down", "next_decision", "Next"),
        Binding("ctrl+up", "prev_decision", "Prev"),
    ]

    def __init__(self, spec: dict, answers_path: Path) -> None:
        super().__init__()
        self.spec = spec
        self.answers_path = answers_path
        self.decisions = spec["decisions"]
        self._all_answered_notified = False
        self.sub_title = f"{spec['title']} · 0/{len(self.decisions)} answered"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(
                *[
                    ListItem(
                        Label(
                            f"{UNANSWERED} {i + 1}. {d['question']}",
                            id=f"navlabel-{i}",
                            markup=True,
                        ),
                        id=f"nav-{i}",
                    )
                    for i, d in enumerate(self.decisions)
                ],
                id="nav",
            )
            with ContentSwitcher(initial="panel-0"):
                for i, d in enumerate(self.decisions):
                    with VerticalScroll(id=f"panel-{i}", classes="panel"):
                        yield Markdown(_decision_md(self.spec, d))
                        dtype = d.get("type", "single")
                        if dtype == "single":
                            widget = RadioSet(
                                *[
                                    DecisionRadio(
                                        _option_label(o),
                                        classes=(
                                            "recommended"
                                            if o.get("recommended")
                                            else ""
                                        ),
                                    )
                                    for o in d["options"]
                                ],
                                DecisionRadio("Other…"),
                                id=f"input-{i}",
                            )
                            widget.border_title = "pick one · enter selects"
                        elif dtype == "multi":
                            widget = SelectionList(
                                *[
                                    Selection(_option_label(o), j)
                                    for j, o in enumerate(d["options"])
                                ],
                                Selection("Other…", OTHER),
                                id=f"input-{i}",
                            )
                            widget.border_title = "pick any · space toggles"
                        else:
                            widget = TextArea(id=f"input-{i}", classes="answer")
                            widget.border_title = "free text"
                        yield widget
                        if dtype in ("single", "multi"):
                            yield Input(
                                placeholder="Other — type your own answer",
                                id=f"other-{i}",
                                classes="other",
                                disabled=True,
                            )
                        yield Input(
                            placeholder="Notes (optional)",
                            id=f"notes-{i}",
                            classes="notes",
                        )
        yield Footer()

    # --- answer collection -------------------------------------------------

    def _collect(self, i: int) -> dict:
        d = self.decisions[i]
        dtype = d.get("type", "single")
        widget = self.query_one(f"#input-{i}")
        notes = self.query_one(f"#notes-{i}", Input).value.strip()
        other = ""
        if dtype == "single":
            idx = widget.pressed_index
            if idx == len(d["options"]):  # the auto-appended Other… button
                selected = []
                other = self.query_one(f"#other-{i}", Input).value.strip()
            elif idx >= 0:
                selected = [option_id(d["options"][idx])]
            else:
                selected = []
        elif dtype == "multi":
            picked = sorted(v for v in widget.selected if isinstance(v, int))
            selected = [option_id(d["options"][j]) for j in picked]
            if OTHER in widget.selected:
                other = self.query_one(f"#other-{i}", Input).value.strip()
        else:
            text = widget.text.strip()
            selected = [text] if text else []
        return {"type": dtype, "selected": selected, "other": other, "notes": notes}

    def _answered(self, i: int) -> bool:
        answer = self._collect(i)
        return bool(answer["selected"] or answer["other"])

    def _refresh_nav(self) -> None:
        n = 0
        for i, d in enumerate(self.decisions):
            answered = self._answered(i)
            n += answered
            mark = ANSWERED if answered else UNANSWERED
            self.query_one(f"#navlabel-{i}", Label).update(
                f"{mark} {i + 1}. {d['question']}"
            )
        self.sub_title = f"{self.spec['title']} · {n}/{len(self.decisions)} answered"
        if not self._all_answered_notified and n == len(self.decisions):
            self._all_answered_notified = True
            self.notify("All answered — ctrl+s to submit", severity="information")

    def _next_unanswered(self, after: int) -> int | None:
        order = list(range(after + 1, len(self.decisions))) + list(range(0, after))
        return next((j for j in order if not self._answered(j)), None)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        idx = int(event.radio_set.id.removeprefix("input-"))
        other_input = self.query_one(f"#other-{idx}", Input)
        if event.radio_set.pressed_index == len(self.decisions[idx]["options"]):
            # Other… chosen: open the text field and wait for typing
            other_input.disabled = False
            other_input.focus()
            self._refresh_nav()
            return
        other_input.disabled = True
        self._refresh_nav()
        # fast keyboard flow: picking an option advances to the next
        # unanswered decision (notes stay reachable by navigating back)
        nxt = self._next_unanswered(idx)
        if nxt is not None:
            self._goto(nxt, focus_input=True)

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        idx = int(event.selection_list.id.removeprefix("input-"))
        other_input = self.query_one(f"#other-{idx}", Input)
        wants_other = OTHER in event.selection_list.selected
        if wants_other and other_input.disabled:
            other_input.disabled = False
            other_input.focus()
        elif not wants_other:
            other_input.disabled = True
        self._refresh_nav()

    def on_text_area_changed(self, _) -> None:
        self._refresh_nav()

    def on_input_changed(self, _) -> None:
        # typing in an Other… field changes answered state live
        self._refresh_nav()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # enter in a filled Other… field advances like picking an option does
        if event.input.id and event.input.id.startswith("other-") and event.input.value.strip():
            idx = int(event.input.id.removeprefix("other-"))
            nxt = self._next_unanswered(idx)
            if nxt is not None:
                self._goto(nxt, focus_input=True)

    # --- navigation --------------------------------------------------------

    def _goto(self, index: int, focus_input: bool = False) -> None:
        self.query_one("#nav", ListView).index = index
        if focus_input:
            self.query_one(f"#input-{index}").focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None:
            index = event.item.id.removeprefix("nav-")
            self.query_one(ContentSwitcher).current = f"panel-{index}"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # enter on a sidebar entry dives into that decision's input
        index = int(event.item.id.removeprefix("nav-"))
        self.query_one(f"#input-{index}").focus()

    def _move(self, delta: int) -> None:
        nav = self.query_one("#nav", ListView)
        if nav.index is not None:
            nav.index = max(0, min(len(self.decisions) - 1, nav.index + delta))

    def action_next_decision(self) -> None:
        self._move(1)

    def action_prev_decision(self) -> None:
        self._move(-1)

    # --- submit / cancel ---------------------------------------------------

    def action_submit(self) -> None:
        answers = {d["id"]: self._collect(i) for i, d in enumerate(self.decisions)}

        def unanswered(d):
            a = answers[d["id"]]
            return d.get("required", True) and not (a["selected"] or a["other"])

        missing = [d["question"] for d in self.decisions if unanswered(d)]
        if missing:
            self.notify(
                "Unanswered: " + "; ".join(missing),
                title="Cannot submit",
                severity="error",
            )
            first = next(i for i, d in enumerate(self.decisions) if unanswered(d))
            self._goto(first, focus_input=True)
            return
        write_answers(self.answers_path, {"status": "submitted", "answers": answers})
        self.exit()

    def action_cancel(self) -> None:
        # two-stage escape: back out to the sidebar first; cancel the whole
        # dashboard only from the sidebar (prevents accidental cancels)
        nav = self.query_one("#nav", ListView)
        if self.focused is not None and self.focused is not nav:
            nav.focus()
            return
        write_answers(self.answers_path, {"status": "cancelled", "answers": {}})
        self.exit()


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--validate":
        try:
            load_spec(Path(args[1]))
        except SpecError as e:
            print(f"INVALID: {e}", file=sys.stderr)
            return 1
        print("OK")
        return 0
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spec_path, answers_path = Path(args[0]), Path(args[1])
    try:
        spec = load_spec(spec_path)
    except SpecError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1

    # If we are killed instead of submitted/cancelled (popup torn down, kill,
    # hangup), still leave a cancelled answers file so the caller's poll ends.
    def _bail(signum, _frame):
        if not answers_path.exists():
            write_answers(answers_path, {"status": "cancelled", "answers": {}})
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _bail)

    app = DecisionApp(spec, answers_path)
    app.run()
    if not answers_path.exists():
        write_answers(answers_path, {"status": "cancelled", "answers": {}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
