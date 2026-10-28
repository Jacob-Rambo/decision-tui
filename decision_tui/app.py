"""decision-tui: render a JSON decision spec as a Textual dashboard, write answers as JSON.

Usage:
    decision-tui <spec.json> <answers.json>
    decision-tui --validate <spec.json>
"""

import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.strip import Strip
from textual.widgets import OptionList
from textual.widgets.option_list import OptionDoesNotExist
from textual.widgets.text_area import TextAreaTheme
from textual.widgets import (
    ContentSwitcher,
    Footer,
    Header,
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

from decision_tui import voice
from decision_tui.spec import SpecError, load_spec, option_id, write_answers

ANSWERED = "[green]●[/green]"
UNANSWERED = "[dim]○[/dim]"

# open cue: a real sound clip beats the terminal bell (which is faint and at
# the mercy of the terminal's bell config). Played detached so it never blocks.
NOTIFY_SOUND = Path(__file__).parent / "assets" / "notify.mp3"
_SOUND_PLAYERS = (
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ["mpv", "--no-video", "--really-quiet"],
    ["gst-play-1.0", "--quiet"],
    ["pw-play"],
)


def _sound_command() -> list | None:
    if not NOTIFY_SOUND.exists():
        return None
    for player in _SOUND_PLAYERS:
        if shutil.which(player[0]):
            return player + [str(NOTIFY_SOUND)]
    return None


_NOTIFY_SOUND_CMD = _sound_command()


def _play_notify_sound() -> bool:
    if not _NOTIFY_SOUND_CMD:
        return False
    try:
        subprocess.Popen(
            _NOTIFY_SOUND_CMD,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


# value of the auto-appended free-text option on every single/multi decision
OTHER = "__other__"

# schemes we'll hand to xdg-open on a link click. file: is included so links to
# LOCAL evidence (screenshots, recordings saved on disk) open in the default
# viewer. Script-execution / data schemes (javascript:, data:, vbscript:) are
# deliberately excluded — they're XSS vectors, not navigation.
_OPENABLE_SCHEMES = ("http://", "https://", "mailto:", "file:")

_LINK_SCHEME_RE = re.compile(r"^\s*([a-z][a-z0-9+.\-]*):", re.IGNORECASE)
_ALLOWED_LINK_SCHEMES = {"http", "https", "mailto", "file"}


def _validate_link(url: str) -> bool:
    """Link validator for markdown rendering: allow our safe schemes (and bare /
    relative / anchor links) to render clickable; reject everything else so it
    stays plain text. markdown-it's default rejects file: — we add it back."""
    match = _LINK_SCHEME_RE.match(url)
    if not match:
        return True  # relative, anchor, or scheme-less
    return match.group(1).lower() in _ALLOWED_LINK_SCHEMES


def _md_parser() -> MarkdownIt:
    """gfm-like parser with linkify ON, so BARE urls in ticket context (Loom,
    page links, screenshot/video urls pasted raw) render as clickable links —
    not just explicit [text](url) markdown. Requires linkify-it-py (a dep). A
    custom validateLink also permits file: links to local evidence."""
    md = MarkdownIt("gfm-like", {"linkify": True}).enable("linkify")
    md.validateLink = _validate_link
    return md


def _open_url(url: str) -> bool:
    """Open a web URL in the user's default browser, fully detached so it never
    blocks the event loop or corrupts the TUI. Returns False for non-web schemes
    or if no opener exists."""
    if not url.lower().startswith(_OPENABLE_SCHEMES):
        return False
    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    try:
        import webbrowser

        return webbrowser.open(url)
    except Exception:  # noqa: BLE001
        return False


def _option_label(option: dict) -> str:
    return option["label"] + (" ★" if option.get("recommended") else "")


# TextArea paints its own document background from its *theme*, not from the
# widget's CSS — so a non-transparent base_style or the cursor-line highlight
# shows as a "black box" the text sits in, regardless of CSS `background`. This
# theme leaves base/gutter/cursor-line styles empty so the dashboard background
# shows straight through, and gives the caret a bright reversed block (no
# foreign-colored box behind it).
TRANSPARENT_TA = TextAreaTheme(
    name="dashboard",
    base_style=RichStyle(),
    gutter_style=RichStyle(),
    cursor_line_style=RichStyle(),
    cursor_line_gutter_style=RichStyle(),
    cursor_style=RichStyle(reverse=True, bold=True),
    selection_style=RichStyle(reverse=True),
)


class GrowingTextArea(TextArea):
    """A TextArea that grows to fit its content instead of scrolling internally.

    Its content height is the soft-wrapped visual line count, so the box's CSS
    `height: auto` sizes it to exactly what's typed/dictated; `min-height` and
    `max-height` clamp it (past max it scrolls again, a safety valve for a giant
    paste). Overriding `_refresh_size` re-runs layout on each edit so the box
    resizes live as text is added or removed.

    ctrl+c clears the field (overriding TextArea's copy binding, and stopping it
    from bubbling to the app's quit handler) — a fast "start over" on one box.
    """

    BINDINGS = [Binding("ctrl+c", "clear_text", "Clear field", show=False)]

    def get_content_height(self, container, viewport, width: int) -> int:
        return max(1, self.wrapped_document.height)

    def _refresh_size(self) -> None:
        super()._refresh_size()
        self.refresh(layout=True)

    def action_clear_text(self) -> None:
        self.text = ""


def _make_textarea(widget_id: str, css_class: str, disabled: bool = False) -> TextArea:
    ta = GrowingTextArea(id=widget_id, classes=css_class, disabled=disabled)
    ta.register_theme(TRANSPARENT_TA)
    ta.theme = "dashboard"
    # key hints on the bottom border, right where the field is
    ta.border_subtitle = "ctrl+r voice · ctrl+c clear"
    return ta


class DecisionRadio(RadioButton):
    """Radio button drawn as ( ) / (●) instead of the block-glyph ▐●▌ default.

    Overrides _button because ToggleButton colors the side glyphs with
    *background* colors (a trick for the half-block ▐▌ characters) which makes
    text glyphs like parens invisible — and because the default draws the same
    inner char for both states, signalling state by color alone.
    """

    BUTTON_LEFT = "("
    BUTTON_RIGHT = ")"

    @property
    def _button(self) -> Content:
        button_style = self.get_visual_style("toggle--button")
        inner = self.BUTTON_INNER if self.value else " "
        return Content.assemble(
            (self.BUTTON_LEFT, button_style),
            (inner, button_style),
            (self.BUTTON_RIGHT, button_style),
        )


class DecisionSelect(SelectionList):
    """Selection list drawn as [ ] / [x] to match the radio glyphs.

    Same reasoning as DecisionRadio: the stock SelectionList draws block-glyph
    checkboxes with bg-colored sides and a state-independent inner char.
    """

    def _get_left_gutter_width(self) -> int:
        return 4  # len("[x] ")

    def render_line(self, y: int) -> Strip:
        line = OptionList.render_line(self, y)
        _, scroll_y = self.scroll_offset
        index = scroll_y + y
        try:
            selection = self.get_option_at_index(index)
        except OptionDoesNotExist:
            return line
        is_selected = selection.value in self._selected
        component_style = "selection-list--button"
        if is_selected:
            component_style += "-selected"
        if self.highlighted == index:
            component_style += "-highlighted"
        underlying_style = next(iter(line)).style or self.rich_style
        glyph_style = self.get_component_rich_style(component_style) + RichStyle(
            meta={"option": index}
        )
        return Strip(
            [
                Segment("[", style=glyph_style),
                Segment("x" if is_selected else " ", style=glyph_style),
                Segment("]", style=glyph_style),
                Segment(" ", style=underlying_style),
                *line,
            ]
        )


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
        padding: 1 0;
        margin-right: 2;
    }
    #nav ListItem {
        padding: 0 1;
        margin: 0 1 1 0;
    }
    /* status + number sit in a fixed gutter so a long question wraps with a
       hanging indent, aligned under its own first word */
    #nav ListItem Horizontal {
        height: auto;
    }
    #nav .navmark {
        width: 6;
    }
    #nav .navtext {
        width: 1fr;
    }
    /* Textual's scrollbar colors are blends that collapse to a solid black
       track and a heavy bright thumb; a thin neutral bar stays out of the way */
    * {
        scrollbar-size-vertical: 1;
        scrollbar-background: ansi_default;
        scrollbar-background-hover: ansi_default;
        scrollbar-background-active: ansi_default;
        scrollbar-color: ansi_bright_black;
        scrollbar-color-hover: ansi_white;
        scrollbar-color-active: ansi_blue;
        scrollbar-corner-color: ansi_default;
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
            color: ansi_default;
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
            color: ansi_default;
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
        height: auto;
        min-height: 4;
        max-height: 30;
        margin: 1 0;
        border: round ansi_bright_black;
        background: ansi_default;
    }
    TextArea.answer:focus {
        border: round ansi_blue;
    }
    TextArea.other, TextArea.notes {
        height: auto;
        min-height: 4;
        max-height: 20;
        background: ansi_default;
        border: round ansi_bright_black;
    }
    TextArea.other {
        margin: 0 0 1 0;
    }
    TextArea.notes {
        margin: 1 0 0 0;
    }
    TextArea.other:focus, TextArea.notes:focus {
        border: round ansi_blue;
    }
    /* the Other box is inert until its option is picked — fade it right back
       so it reads as inactive, then snaps to full strength when enabled */
    TextArea.other:disabled {
        opacity: 45%;
    }
    /* the question heading: default h2 is underlined primary, which reads as
       a link next to the real (blue, underlined) links */
    MarkdownH2 {
        color: ansi_default;
        text-style: bold;
        margin: 1 0 1 0;
    }
    /* blockquotes (ticket text, comments) and code fences default to blended
       backgrounds that collapse to a solid black slab; an accent rule on the
       left carries the "quoted" signal with the terminal's own bg and fg */
    MarkdownBlockQuote {
        background: ansi_default;
        color: ansi_default;
        border-left: outer ansi_blue;
        padding: 0 2;
    }
    MarkdownFence {
        background: ansi_default;
        color: ansi_default;
        border: round ansi_bright_black;
        padding: 0 1;
    }
    MarkdownTableContent {
        keyline: thin ansi_bright_black;
    }
    MarkdownTableContent > .header {
        color: ansi_blue;
    }
    Markdown {
        link-color: ansi_blue;
        link-style: underline;
        link-color-hover: ansi_default;
        link-background-hover: ansi_blue;
        link-style-hover: bold;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "submit", "Submit all"),
        Binding("escape", "cancel", "Back / Cancel"),
        Binding("ctrl+down", "next_decision", "Next"),
        Binding("ctrl+up", "prev_decision", "Prev"),
        Binding("ctrl+r", "voice", "Voice"),
    ]

    def __init__(self, spec: dict, answers_path: Path) -> None:
        super().__init__()
        self.spec = spec
        self.answers_path = answers_path
        self.decisions = spec["decisions"]
        self._all_answered_notified = False
        # voice dictation state
        self._recording: voice.Recording | None = None
        self._voice_target: TextArea | None = None
        self._voice_orig_title = ""
        self._transcribing = False
        self.sub_title = f"{spec['title']} · 0/{len(self.decisions)} answered"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(
                *[
                    ListItem(
                        Horizontal(
                            Label(
                                f"{UNANSWERED} {i + 1}.",
                                id=f"navmark-{i}",
                                classes="navmark",
                                markup=True,
                            ),
                            Label(d["question"], classes="navtext", markup=False),
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
                        yield Markdown(
                            _decision_md(self.spec, d),
                            parser_factory=_md_parser,
                            open_links=False,  # our guarded handler opens links
                        )
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
                        elif dtype == "bool":
                            widget = DecisionSelect(
                                Selection(d.get("label") or "Yes", "true"),
                                id=f"input-{i}",
                            )
                            widget.border_title = "check = true · space toggles"
                        elif dtype == "multi":
                            widget = DecisionSelect(
                                *[
                                    Selection(_option_label(o), j)
                                    for j, o in enumerate(d["options"])
                                ],
                                Selection("Other…", OTHER),
                                id=f"input-{i}",
                            )
                            widget.border_title = "pick any · space toggles"
                        else:
                            widget = _make_textarea(f"input-{i}", "answer")
                            widget.border_title = "free text"
                        yield widget
                        if dtype in ("single", "multi"):
                            other_ta = _make_textarea(
                                f"other-{i}", "other", disabled=True
                            )
                            other_ta.border_title = "other — your own answer"
                            yield other_ta
                        notes_ta = _make_textarea(f"notes-{i}", "notes")
                        notes_ta.border_title = "notes (optional)"
                        yield notes_ta
        yield Footer()

    def on_mount(self) -> None:
        # bools count as answered (false) from the start — sync the sidebar
        # dots and header count with reality before any interaction
        self._refresh_nav()
        # announce that a dashboard has opened and needs attention; fall back to
        # the terminal bell only if no audio player is available
        if not _play_notify_sound():
            self.bell()
        # warm the speech model in the background so the first ctrl+r doesn't
        # pay the cold-start load; harmless no-op if voice isn't available
        if voice.voice_available()[0]:
            self._prewarm_voice()

    # --- voice dictation ---------------------------------------------------

    @work(thread=True, group="voice-prewarm", exit_on_error=False)
    def _prewarm_voice(self) -> None:
        try:
            voice.preload()
        except Exception:
            pass  # voice stays optional; failures surface on actual use

    def action_voice(self) -> None:
        target = self.focused
        if self._transcribing:
            return  # a transcription is already in flight
        if self._recording is not None:
            self._stop_and_transcribe()
            return
        if not isinstance(target, TextArea):
            self.notify(
                "Focus a text field, then press ctrl+r to dictate into it",
                severity="warning",
            )
            return
        usable, reason = voice.voice_available()
        if not usable:
            self.notify(f"Voice unavailable: {reason}", severity="error")
            return
        try:
            self._recording = voice.Recording()
        except Exception as e:  # noqa: BLE001 — surface any capture failure
            self.notify(f"Could not start recording: {e}", severity="error")
            return
        self._voice_target = target
        self._voice_orig_title = target.border_title or ""
        target.border_title = "● REC — ctrl+r to stop"
        self.notify("Recording… press ctrl+r to stop and transcribe")

    def _stop_and_transcribe(self) -> None:
        rec, target = self._recording, self._voice_target
        self._recording = None
        wav = rec.stop() if rec else None
        if target is None:
            return
        if not wav:
            target.border_title = self._voice_orig_title
            self.notify("No audio captured", severity="warning")
            return
        self._transcribing = True
        target.border_title = "transcribing…"
        self._transcribe_worker(wav, target)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _transcribe_worker(self, wav: str, target: TextArea) -> None:
        try:
            text = voice.transcribe(wav)
            self.call_from_thread(self._insert_transcript, target, text)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._voice_failed, target, str(e))

    def _insert_transcript(self, target: TextArea, text: str) -> None:
        self._transcribing = False
        target.border_title = self._voice_orig_title
        if not text:
            self.notify("Heard nothing — try again", severity="warning")
            return
        # space the dictation off from any existing text unless we're at a clean
        # boundary, so back-to-back dictations don't run words together
        existing = target.text
        if existing and not existing[-1:].isspace():
            text = " " + text
        target.focus()
        target.insert(text)
        self._refresh_nav()

    def _voice_failed(self, target: TextArea, msg: str) -> None:
        self._transcribing = False
        target.border_title = self._voice_orig_title
        self.notify(f"Transcription failed: {msg}", severity="error")

    # --- links -------------------------------------------------------------

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        # click a link in any decision's context (Loom, page, screenshot, etc.)
        # and it opens in the default browser on Jake's desktop
        url = event.href
        if _open_url(url):
            self.notify(f"Opening {url}", title="link")
        else:
            self.notify(f"Won't open non-web URL: {url}", severity="warning")

    # --- answer collection -------------------------------------------------

    def _collect(self, i: int) -> dict:
        d = self.decisions[i]
        dtype = d.get("type", "single")
        widget = self.query_one(f"#input-{i}")
        notes = self.query_one(f"#notes-{i}", TextArea).text.strip()
        other = ""
        if dtype == "single":
            idx = widget.pressed_index
            if idx == len(d["options"]):  # the auto-appended Other… button
                selected = []
                other = self.query_one(f"#other-{i}", TextArea).text.strip()
            elif idx >= 0:
                selected = [option_id(d["options"][idx])]
            else:
                selected = []
        elif dtype == "bool":
            # a checkbox is never unanswered: unchecked IS false
            selected = ["true"] if "true" in widget.selected else ["false"]
        elif dtype == "multi":
            picked = sorted(v for v in widget.selected if isinstance(v, int))
            selected = [option_id(d["options"][j]) for j in picked]
            if OTHER in widget.selected:
                other = self.query_one(f"#other-{i}", TextArea).text.strip()
        else:
            text = widget.text.strip()
            selected = [text] if text else []
        return {"type": dtype, "selected": selected, "other": other, "notes": notes}

    def _answered(self, i: int) -> bool:
        answer = self._collect(i)
        return bool(answer["selected"] or answer["other"])

    def _refresh_nav(self) -> None:
        n = 0
        for i in range(len(self.decisions)):
            answered = self._answered(i)
            n += answered
            mark = ANSWERED if answered else UNANSWERED
            self.query_one(f"#navmark-{i}", Label).update(f"{mark} {i + 1}.")
        self.sub_title = f"{self.spec['title']} · {n}/{len(self.decisions)} answered"
        if not self._all_answered_notified and n == len(self.decisions):
            self._all_answered_notified = True
            self.notify("All answered — ctrl+s to submit", severity="information")

    def _next_unanswered(self, after: int) -> int | None:
        order = list(range(after + 1, len(self.decisions))) + list(range(0, after))
        return next((j for j in order if not self._answered(j)), None)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        idx = int(event.radio_set.id.removeprefix("input-"))
        d = self.decisions[idx]
        if d.get("type", "single") == "single":  # bool radio sets have no Other…
            other_input = self.query_one(f"#other-{idx}", TextArea)
            if event.radio_set.pressed_index == len(d["options"]):
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
        if self.decisions[idx].get("type", "single") == "multi":  # bools have no Other…
            other_input = self.query_one(f"#other-{idx}", TextArea)
            wants_other = OTHER in event.selection_list.selected
            if wants_other and other_input.disabled:
                other_input.disabled = False
                other_input.focus()
            elif not wants_other:
                other_input.disabled = True
        self._refresh_nav()

    def on_text_area_changed(self, _) -> None:
        # answer textareas and Other…/notes fields all affect answered state
        self._refresh_nav()

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
