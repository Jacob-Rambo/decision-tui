"""Headless smoke test: drive the app with Textual's pilot, assert the answers file."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from textual.widgets import RadioSet, SelectionList, TextArea

from decision_tui.app import DecisionApp
from decision_tui.spec import load_spec

ROOT = Path(__file__).resolve().parent.parent


async def run() -> None:
    spec = load_spec(ROOT / "examples" / "sample-spec.json")
    answers_path = Path(tempfile.mkdtemp()) / "answers.json"
    app = DecisionApp(spec, answers_path)
    async with app.run_test() as pilot:
        # submit with nothing answered must be refused
        app.action_submit()
        await pilot.pause()
        assert not answers_path.exists(), "submit should be blocked while required decisions are unanswered"

        # decision 0 (single): pick option index 2 (canary)
        radio = app.query_one("#input-0", RadioSet)
        radio.query("RadioButton")[2].value = True
        # decision 1 (multi): pick options 0 and 2
        sel = app.query_one("#input-1", SelectionList)
        sel.select(0)
        sel.select(2)
        # decision 2 (bool): leave the checkbox untouched — unchecked IS false
        boolbox = app.query_one("#input-2", SelectionList)
        assert boolbox.option_count == 1, "bool renders one checkbox row, no Other…"
        # decision 3 (text): leave empty — it's optional
        assert isinstance(app.query_one("#input-3", TextArea), TextArea)
        await pilot.pause()

        app.action_submit()
        await pilot.pause()

    data = json.loads(answers_path.read_text())
    assert data["status"] == "submitted", data
    a = data["answers"]
    assert a["deploy-strategy"]["selected"] == ["canary"], a
    assert a["feature-flags"]["selected"] == ["new-editor", "dark-mode"], a
    assert a["tag-stable"]["selected"] == ["false"], a
    assert a["release-notes"]["selected"] == [], a

    # cancel path writes a cancelled answers file
    answers_path2 = Path(tempfile.mkdtemp()) / "answers.json"
    app2 = DecisionApp(spec, answers_path2)
    async with app2.run_test() as pilot:
        app2.action_cancel()
        await pilot.pause()
    assert json.loads(answers_path2.read_text())["status"] == "cancelled"

    # Other… path: free-text answers on single and multi decisions
    from textual.widgets import Input

    from decision_tui.app import OTHER

    answers_path3 = Path(tempfile.mkdtemp()) / "answers.json"
    app3 = DecisionApp(spec, answers_path3)
    async with app3.run_test() as pilot:
        # decision 0 (single): pick the auto-appended Other… button (index 3)
        radio = app3.query_one("#input-0", RadioSet)
        buttons = radio.query("RadioButton")
        assert len(buttons) == 4, "Other… button should be auto-appended"
        buttons[3].value = True
        await pilot.pause()
        other0 = app3.query_one("#other-0", Input)
        assert not other0.disabled, "Other input should enable when Other… is picked"

        # submit with Other chosen but empty must be refused
        app3.action_submit()
        await pilot.pause()
        assert not answers_path3.exists(), "empty Other text must not count as answered"

        other0.value = "ship it behind a header flag"
        # decision 1 (multi): toggle Other… alongside a real option
        sel = app3.query_one("#input-1", SelectionList)
        sel.select(0)
        sel.select(OTHER)
        await pilot.pause()
        other1 = app3.query_one("#other-1", Input)
        assert not other1.disabled
        other1.value = "enable-metrics-flag"
        # decision 2 (bool): check the box → true
        app3.query_one("#input-2", SelectionList).select("true")
        await pilot.pause()

        app3.action_submit()
        await pilot.pause()

    data3 = json.loads(answers_path3.read_text())
    assert data3["status"] == "submitted", data3
    a3 = data3["answers"]
    assert a3["deploy-strategy"]["selected"] == [], a3
    assert a3["deploy-strategy"]["other"] == "ship it behind a header flag", a3
    assert a3["feature-flags"]["selected"] == ["new-editor"], a3
    assert a3["feature-flags"]["other"] == "enable-metrics-flag", a3
    assert a3["tag-stable"]["selected"] == ["true"], a3

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(run())
    sys.exit(0)
