"""Decision spec loading and validation.

A spec is a JSON document:

{
  "title": "Deploy review",                  # required
  "context_md": "markdown shown on every decision panel",  # optional
  "decisions": [                             # required, non-empty
    {
      "id": "deploy-strategy",               # required, unique
      "question": "Which deploy strategy?",  # required
      "type": "single",                      # single | multi | bool | text (default: single)
      "required": true,                      # default true
      "labels": ["True", "False"],           # bool only, optional: display labels for the pair
      "context_md": "markdown: tables, comparisons, evidence",  # optional
      "options": [                           # required for single/multi
        {
          "id": "blue-green",                # optional, falls back to label
          "label": "Blue-green",             # required
          "description": "why / tradeoffs",  # optional
          "recommended": false               # optional
        }
      ]
    }
  ]
}

Every single/multi decision automatically gets an "Other…" free-text option
appended by the renderer (like AskUserQuestion) — never define your own Other
option in a spec.

bool decisions take no options: they render as a True/False radio pair (display
labels overridable via "labels") and answer with canonical "true"/"false" ids
regardless of the labels. No Other option — a bool is a closed proposition;
nuance goes in notes.

Answers are written as:

{
  "status": "submitted" | "cancelled",
  "answers": {
    "<decision id>": {
      "type": "single",
      "selected": ["blue-green"],            # option ids; for text: [<the text>]
      "other": "",                           # text typed into the Other… option, if chosen
      "notes": "free-form note or empty"
    }
  }
}

A required decision counts as answered when selected is non-empty OR other is
non-empty.
"""

import json
from pathlib import Path

VALID_TYPES = ("single", "multi", "bool", "text")


class SpecError(ValueError):
    pass


def load_spec(path: Path) -> dict:
    try:
        spec = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SpecError(f"{path}: invalid JSON: {e}") from e
    validate_spec(spec)
    return spec


def validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    if not spec.get("title"):
        raise SpecError("spec.title is required")
    decisions = spec.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise SpecError("spec.decisions must be a non-empty array")
    seen_ids = set()
    for i, d in enumerate(decisions):
        where = f"decisions[{i}]"
        if not isinstance(d, dict):
            raise SpecError(f"{where} must be an object")
        if not d.get("id"):
            raise SpecError(f"{where}.id is required")
        if d["id"] in seen_ids:
            raise SpecError(f"{where}.id {d['id']!r} is duplicated")
        seen_ids.add(d["id"])
        if not d.get("question"):
            raise SpecError(f"{where}.question is required")
        dtype = d.get("type", "single")
        if dtype not in VALID_TYPES:
            raise SpecError(f"{where}.type must be one of {VALID_TYPES}, got {dtype!r}")
        if dtype == "bool":
            labels = d.get("labels")
            if labels is not None and (
                not isinstance(labels, list)
                or len(labels) != 2
                or not all(isinstance(s, str) and s for s in labels)
            ):
                raise SpecError(f"{where}.labels must be two non-empty strings")
        if dtype in ("single", "multi"):
            options = d.get("options")
            if not isinstance(options, list) or not options:
                raise SpecError(f"{where}.options must be a non-empty array for type {dtype!r}")
            opt_ids = set()
            for j, o in enumerate(options):
                if not isinstance(o, dict) or not o.get("label"):
                    raise SpecError(f"{where}.options[{j}].label is required")
                oid = option_id(o)
                if oid in opt_ids:
                    raise SpecError(f"{where}.options[{j}] id/label {oid!r} is duplicated")
                opt_ids.add(oid)


def option_id(option: dict) -> str:
    return option.get("id") or option["label"]


def write_answers(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.rename(path)
