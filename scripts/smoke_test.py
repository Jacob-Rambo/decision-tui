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
        # decision 2 (text): leave empty — it's optional
        assert isinstance(app.query_one("#input-2", TextArea), TextArea)
        await pilot.pause()

        app.action_submit()
        await pilot.pause()

    data = json.loads(answers_path.read_text())
    assert data["status"] == "submitted", data
    a = data["answers"]
    assert a["deploy-strategy"]["selected"] == ["canary"], a
    assert a["feature-flags"]["selected"] == ["new-editor", "dark-mode"], a
    assert a["release-notes"]["selected"] == [], a

    # cancel path writes a cancelled answers file
    answers_path2 = Path(tempfile.mkdtemp()) / "answers.json"
    app2 = DecisionApp(spec, answers_path2)
    async with app2.run_test() as pilot:
        app2.action_cancel()
        await pilot.pause()
    assert json.loads(answers_path2.read_text())["status"] == "cancelled"

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(run())
    sys.exit(0)
